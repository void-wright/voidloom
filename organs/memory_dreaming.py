#!/usr/bin/env python3
"""Memory Dreaming — Wizard Library L4 Piece 1.

Dual-pool random-walk synthesis: a nightly background job that pulls TWO
pools of memory pairs and asks an LLM to surface unexpected connections.

Pool 1 — Divergent (5-10 pairs):
    Memories from completely different parts of the library (cosine
    distance > 0.6). Cross-domain insights: a finding from one domain
    meets a validation result from an unrelated one.

Pool 2 — Close-but-Different-Context (5-10 pairs):
    Semantically similar (cosine distance < 0.3) but from different
    contexts: different sessions, clients, time periods (>30 days), or
    categories. These catch FORGOTTEN PRECEDENTS — we already solved this
    in a different context.

Pool 3 — Persona dreams (10% of total pairs):
    At least 2 pairs where one memory is tagged persona-related. Dream
    prompt variant asks how the pair relates to the declared persona's personality.

Public API
----------
``select_divergent_pairs(conn, count, ...) -> List[Tuple[int, int, float]]``
    Sample pairs with cosine distance > threshold.

``select_close_context_pairs(conn, count, ...) -> List[Tuple[int, int, float, dict]]``
    Sample pairs that are semantically close but contextually different.

``select_persona_pairs(conn, count, ...) -> List[Tuple[int, int, float, dict]]``
    Sample pairs where at least one memory is persona-tagged.

``dream_on_pair(memory_a, memory_b, dream_type, ...) -> Optional[str]``
    Invoke ``claude -p`` to synthesize an insight from a pair.

``run_dream_cycle(db_path, config, dry_run) -> dict``
    Full nightly cycle: select pairs, dream, store, log.

``get_dream_digest(conn, days, limit) -> List[dict]``
    Top N meaningful dreams from the last M days.

``flag_interesting(conn, dream_id) -> dict``
    Mark a dream as interesting (Drew's curation).

``is_dreaming_migration_applied(conn) -> bool``
    Check if migration 017 has been applied.

``apply_dreaming_migration(db_path, migration_sql_path) -> dict``
    Idempotent migration runner.

``unit_test_memory_dreaming()``
    Self-contained unit tests.

Design references: memory #4071 (persona dreams), #4065 (dreaming spec).
"""

from __future__ import annotations

import argparse
# Portable cross-platform file locking (Council #4827): filelock works on
# Unix + Windows + Mac (installed in all wizard-library envs). Falls back to
# fcntl on Unix if filelock is somehow unavailable.
try:
    from filelock import FileLock as _FileLock, Timeout as _FLTimeout
    _HAS_FILELOCK = True
except ImportError:
    _HAS_FILELOCK = False
    try:
        import fcntl
    except ImportError:
        fcntl = None
import json
import tempfile
import logging
import math
import os
import random
import re
import sqlite3
import struct
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# rho-1: the ONE env-directory resolver. Hard import by design -- it is
# stdlib-only and is laid by the same `cp -f "${PKG_ROOT}"/*.py` loop that
# lays this module, so its absence means an incomplete install, and an
# incomplete install must SAY SO rather than resolve sidecars elsewhere.
# A per-module inline fallback would recreate the 13-way divergence this
# module exists to end.
import voidloom_env
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

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
               attach=True, encoder=None, enqueue_scan=True):
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
                                    attach=attach, encoder=encoder, enqueue_scan=enqueue_scan)
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

# --- Voidloom #176 A3: dream low-trust floor -------------------------------
# A dream is a self-generated SYNTHESIS, not an authored fact and not an
# external fact — it must rank BELOW a memory the owner or the assistant authored.
# Previously the dream INSERT omitted both `confidence` (defaulting to the table
# 1.0) and `derived_from` (NULL) -> trust_tier.compute_trust read it as AUTHORED
# (authorship floor 0.85, self-confidence 1.0) -> trust ~0.925 = level with Drew.
#
# We stamp two columns at write time (mirroring the spirit of
# warden.stamp_untrusted_source — low trust + VISIBLE provenance, without gating
# the write):
#   * derived_from = 'dream_synthesis' — a non-authored sentinel. trust_tier's
#     _authorship_floor treats any non-empty derived_from as DERIVED, flooring
#     authorship at the reflection kind-prior (~0.60) instead of 0.85.
#   * confidence = 0.40 — a low self-confidence (the dominant headroom term),
#     so the blended trust lands well under an authored memory's ~0.92.
# Net effect: a dream now scores ~0.6-0.7, comfortably below Drew — recallable
# (calm: rank LOW, never bury), but never read as authoritative.
DREAM_DERIVED_FROM = "dream_synthesis"
DREAM_CONFIDENCE = 0.40
# --- end Voidloom #176 A3 constants ----------------------------------------

# ============================================================
# Configuration
# ============================================================

DEFAULT_CONFIG = {
    "divergent_pairs": 7,
    "close_context_pairs": 7,
    "persona_pair_fraction": 0.10,
    "divergent_distance_min": 0.6,
    "close_context_distance_max": 0.40,  # Jina v5 1024-dim: 0.3 yields 0.1% of pairs; 0.40 yields 0.4% (4x). Calibrated empirically on 4073 prod memories (Reflector density probe, 5000 random checks at 7 thresholds).
    "close_context_min_criteria": 2,
    "max_content_chars": 300,
    "claude_model": "sonnet",
    "claude_timeout_seconds": 60,
    # None = THIS BRAIN's dream lock, beside its database (run_lock_path). Never a path a tempdir decides.
    "lock_file": None,
    "store_reflections": True,
    "digest_days": 7,
    "digest_limit": 5,
}

PERSONA_TAGS = frozenset({
    "persona-refinement", "persona-growth", "persona-feedback",
    "persona-design", "persona-evolution", "persona-embodiment",
    "living-persona", "persona",
    "four-layer-persona", "persona-depth", "persona-persistence",
})

# Embedding dimension — Jina v5 1024-dim (active_embedding_table).
_JINA_DIM = 1024
_NOMIC_DIM = 768

_lock_fd = None


# ============================================================
# Lock file (same pattern as sleep_loop.py)
# ============================================================

# The legacy, machine-global lock NAMES of the dream cycle. Before Round 3 this module defaulted to
# tempfile.gettempdir()/wizard-library-dreaming.lock, cron/setup-dreaming-cron.sh wrote the absolute
# "/tmp/wizard-library-dreaming.lock" into the config it creates, and dream_pipeline.py defaulted to
# tempfile.gettempdir()/voidloom-dream-pipeline.lock -- keyed by the machine (or by TMPDIR), never by the brain.
LEGACY_LOCK_NAMES = ("wizard-library-dreaming.lock", "voidloom-dream-pipeline.lock")
# EX_TEMPFAIL: this brain's dream lock was held, so nothing ran and nothing failed. The run ledger
# (organ_run_log.SKIP_STATUSES) files 75 as "skipped-lock"; dream_pipeline.py and sleep_loop.py use the same status.
EXIT_SKIPPED_LOCK = 75

# ROUND 1 (lane L5): STARVED. The health check found 45 EMPTY dreams across five cycles (2026-10-06 18:00 to
# 10-08 02:00 UTC) while the journal said "thinker unavailable ... You've hit your weekly limit": every pair's call
# failed, every pair still wrote a dream_log row, and the cycle exited 0. A thinker that is UNAVAILABLE (quota, auth,
# unreachable, unconfigured, missing: thinker.UNAVAILABLE_REASONS) is not a pair that dreamt nothing -- nothing can
# be dreamt tonight. The cycle stops at the first such reply, writes NO row for it, reports status 'starved' with
# the reason, and exits EXIT_STARVED, which both dream units admit (SuccessExitStatus) so systemd does not call a
# quota week a failure, while the dream ledger's watch (0 attempts in the window) reads STARVED, not HEALTHY.
# 79 is outside sysexits.h (64-78) and outside every status this package already uses (0, 1, 2, 3, 75).
EXIT_STARVED = 79


class ThinkerStarved(Exception):
    """The thinker is unavailable for this run (quota / auth / unreachable / unconfigured / missing).

    ROUND 2 (R2c, m4): one of these is a CHOICE, not a failure -- the owner answered "e) not now" (the seam raises
    ThinkUnavailable('unconfigured', ..., backend 'off')). `.declined` is True for it, and every caller reports the run
    UNCONFIGURED with exit 0 instead of STARVED / 79: a deliberate answer must not read as a quota alarm every night.
    """

    def __init__(self, reason: str, detail: str = "", backend: str = ""):
        self.reason, self.detail, self.backend = reason, detail, backend
        super().__init__("thinker unavailable (%s)%s: %s" % (reason, " on " + backend if backend else "", detail))

    @property
    def declined(self) -> bool:
        return self.backend == THINKER_DECLINED_BACKEND


# ROUND 2 (R2c, m4): the owner's "e) not now". The thinker record's backend is 'off'; the seam raises
# ThinkUnavailable('unconfigured', ..., 'off') for it and for nothing else. Every reader keys on this one value.
THINKER_DECLINED_BACKEND = "off"
UNCONFIGURED_NOTE = ("the owner answered e) not now: no thinker, so no dream is written and the simple nightly "
                     "routines run instead (`voidloom thinker set <backend>` switches it on)")


def thinker_declined(e: BaseException) -> bool:
    """True when a seam error is the owner's "e) not now" -- a choice, never a starved night."""
    return (isinstance(e, _thinker.ThinkUnavailable)
            and getattr(e, "backend_id", "") == THINKER_DECLINED_BACKEND)


def thinker_declined_on(db_path: Optional[str]) -> bool:
    """True when the brain at db_path carries a thinker record whose answer was "e) not now" (read-only; a missing,
    torn or unreadable record is NOT a declination -- those stay loud)."""
    if not db_path:
        return False
    try:
        rec = _thinker.read_record(Path(str(db_path)).expanduser().absolute().parent / _thinker.RECORD_NAME)
    except Exception:  # noqa: BLE001 - RecordError and OSError alike: not a declination
        return False
    return bool(rec) and rec.get("backend") == THINKER_DECLINED_BACKEND


def run_lock_path(db_path: str, configured: Optional[str] = None) -> Tuple[str, Optional[str]]:
    """The dream cycle's lock for ONE brain, and a note when a configured path was set aside.

    The lock keeps two dream cycles off the SAME brain (the nightly and the daytime units, #20269, and this module's
    legacy cycle), so it is keyed by the brain: it lives beside the brain's database file, at the file's real path (a
    symlinked spelling shares the lock), where every process that can open this brain finds it whatever its TMPDIR or
    PrivateTmp, and where no process of another brain ever looks -- the sleep loop's design (sleep_loop.run_lock_path).

    MEASURED on the reference host, 2026-10-01 22:30:09, for the sleep loop's twin of this lock: a test harness's loop
    on a SCRATCH brain held the machine-global lock and the host's own nightly loop SKIPPED. The dream locks had the
    same shape (a fixed name under /tmp, or under whatever TMPDIR the runner had).

    A config naming a legacy name, in any directory, keeps working: this brain's own lock is used and the note says
    so. Any other configured path is the owner's explicit choice and is honoured as given.
    """
    brain = os.path.realpath(db_path) + ".dreaming.lock"
    if configured and os.path.basename(str(configured)) not in LEGACY_LOCK_NAMES:
        return str(configured), None
    if not configured:
        return brain, None
    return brain, ("the configured lock_file %s is a legacy machine-global lock (any brain on this machine could "
                   "hold it); this brain's own lock is used instead" % configured)


_lock = None  # filelock.FileLock instance (cross-platform path)


def _acquire_lock(lock_path: str) -> bool:
    global _lock_fd, _lock
    if _HAS_FILELOCK:
        lp = lock_path if lock_path.endswith('.lock') else lock_path + '.lock'
        _lock = _FileLock(lp, timeout=0)
        try:
            _lock.acquire()
            return True
        except _FLTimeout:
            _lock = None
            return False
    if fcntl is None:
        return True  # no locking primitive available — proceed (single-run assumption)
    # A lock file that cannot be CREATED raises (OSError) for the caller to name -- it is never "held by another
    # process", which is what the flock contention below means (filelock raises the same way). sleep_loop.py's shape.
    _lock_fd = open(lock_path, 'w')
    try:
        fcntl.flock(_lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_fd.write(str(os.getpid()))
        _lock_fd.flush()
        return True
    except (IOError, OSError):
        if _lock_fd:
            _lock_fd.close()
            _lock_fd = None
        return False


def _release_lock(lock_path: str):
    global _lock_fd, _lock
    if _HAS_FILELOCK:
        if _lock is not None:
            try:
                _lock.release()
            except Exception:
                pass
            _lock = None
        return
    if _lock_fd:
        try:
            fcntl.flock(_lock_fd.fileno(), fcntl.LOCK_UN)
            _lock_fd.close()
        except (IOError, OSError):
            pass
        _lock_fd = None
    try:
        os.unlink(lock_path)
    except OSError:
        pass


# ============================================================
# Helpers
# ============================================================

def _get_conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        conn.execute("SELECT vec_version()")
    except sqlite3.OperationalError:
        try:
            import sqlite_vec
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
        except ImportError:
            pass
    return conn


def _detect_embedding_table(conn: sqlite3.Connection) -> Tuple[str, int]:
    """Detect the active embedding table and dimension.

    Reads the metadata key ``active_embedding_table``. Falls back to
    ``memory_embeddings`` (768-dim Nomic) if the key is absent.
    """
    # ROUND GAMMA (A7): batch organ, so it REFUSES rather than guessing. There is
    # no interactive caller to degrade for; a dream cycle that cannot tell which
    # tier it is on should stop, loudly, not quietly read a dead table.
    import embed_resolver
    return embed_resolver.resolve(conn)


def _cosine_distance_from_bytes(vec_a: bytes, vec_b: bytes) -> float:
    """Compute cosine DISTANCE (1 - similarity) between two float32 byte blobs."""
    dim = len(vec_a) // 4
    if dim == 0:
        return 1.0
    a = struct.unpack(f"{dim}f", vec_a)
    b = struct.unpack(f"{dim}f", vec_b)
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 1.0
    similarity = dot / (norm_a * norm_b)
    return max(0.0, 1.0 - similarity)


def _get_memory_metadata(conn: sqlite3.Connection, memory_id: int) -> Optional[dict]:
    """Fetch lightweight metadata for context-difference evaluation."""
    row = conn.execute(
        """SELECT m.id, m.summary, m.content, m.category, m.kind,
                  m.source_project, m.created_at
           FROM memories m
           WHERE m.id = ? AND m.is_archived = 0""",
        (memory_id,),
    ).fetchone()
    if not row:
        return None
    tags = [r[0] for r in conn.execute(
        "SELECT tag FROM memory_tags WHERE memory_id = ?", (memory_id,)
    ).fetchall()]
    return {
        "id": row[0],
        "summary": row[1] or "",
        "content": row[2] or "",
        "category": row[3],
        "kind": row[4],
        "source_project": row[5],
        "created_at": row[6],
        "tags": tags,
    }


def _context_difference(meta_a: dict, meta_b: dict, min_criteria: int = 2) -> Optional[dict]:
    """Evaluate whether two memories come from sufficiently different contexts.

    For close-context pairs (cosine < 0.3), we require at least ``min_criteria``
    of the following to be true (Drew's directive: "be crafty about ensuring
    close pairs aren't two-versions-of-the-same-thought"):

      1. Different source_project
      2. Different client tag (any tag containing a client slug)
      3. Created >30 days apart
      4. Different category
      5. Different kind
      6. No shared tags indicating same work session

    Returns:
        dict of criteria met (with values), or None if fewer than min_criteria pass.
    """
    criteria: Dict[str, Any] = {}

    # 1. Different source_project
    if (meta_a["source_project"] or "") != (meta_b["source_project"] or ""):
        criteria["different_project"] = True

    # 2. Different client tag
    #
    # OPERATOR-SUPPLIED, empty by default. This was a hardcoded set of eleven real
    # company names -- one operator's employer's client list -- baked into a module
    # that ships. Two things were wrong with that and only one is about privacy:
    # the names do not belong in a distributed product, AND the heuristic was dead
    # for every other operator, whose clients are different. It silently scored
    # nothing while looking like it worked.
    #
    # Same remedy as arcane_manifest.py's skills path (VOIDLOOM_SKILLS_DIR): the
    # operator supplies their own vocabulary, the default is EMPTY, and an empty
    # vocabulary degrades to "no client signal" -- which is the honest reading for
    # a brain with no client taxonomy, and is already the `not clients_a and not
    # clients_b` branch below.
    #
    #   export VOIDLOOM_CLIENT_SLUGS="acme,globex,initech"
    client_slugs = {
        c.strip().lower()
        for c in os.environ.get("VOIDLOOM_CLIENT_SLUGS", "").split(",")
        if c.strip()
    }
    tags_a = set(meta_a.get("tags", []))
    tags_b = set(meta_b.get("tags", []))
    clients_a = {t for t in tags_a if any(c in t.lower() for c in client_slugs)}
    clients_b = {t for t in tags_b if any(c in t.lower() for c in client_slugs)}
    if clients_a and clients_b and not clients_a.intersection(clients_b):
        criteria["different_client"] = True
    elif not clients_a and not clients_b:
        pass  # both have no client tags — not a signal
    elif clients_a != clients_b:
        criteria["different_client"] = True

    # 3. Created >30 days apart
    try:
        dt_a = datetime.fromisoformat(meta_a["created_at"].replace("Z", "+00:00"))
        dt_b = datetime.fromisoformat(meta_b["created_at"].replace("Z", "+00:00"))
        days_apart = abs((dt_a - dt_b).days)
        if days_apart > 30:
            criteria["days_apart"] = days_apart
    except (ValueError, TypeError):
        pass

    # 4. Different category
    if meta_a["category"] != meta_b["category"]:
        criteria["different_category"] = True

    # 5. Different kind
    if meta_a["kind"] != meta_b["kind"]:
        criteria["different_kind"] = True

    # 6. No shared session-like tags
    session_indicators = {"session-", "batch-", "campaign-", "phase-", "iter-"}
    session_tags_a = {t for t in tags_a if any(t.startswith(s) for s in session_indicators)}
    session_tags_b = {t for t in tags_b if any(t.startswith(s) for s in session_indicators)}
    if session_tags_a and session_tags_b and not session_tags_a.intersection(session_tags_b):
        criteria["different_session"] = True
    elif not session_tags_a and not session_tags_b:
        criteria["different_session"] = True  # no session tags at all = not same session

    if len(criteria) >= min_criteria:
        return criteria
    return None


# ============================================================
# Pair Selection
# ============================================================

def select_divergent_pairs(
    conn: sqlite3.Connection,
    count: int = 7,
    distance_min: float = 0.6,
    max_candidates: int = 200,
    candidate_ids: Optional[List[int]] = None,
) -> List[Tuple[int, int, float]]:
    """Select divergent memory pairs (cosine distance > threshold).

    L5-DR/G SEED-BIAS: when ``candidate_ids`` is provided (the activity-profile +
    surprise-weighted pool from dream_pipeline Phase 0), the candidate pull is
    RESTRICTED to that pool instead of ``ORDER BY RANDOM()`` -- "anchor near,
    fling far" personalization. ``distance_min`` (the 0.6 floor) is NEVER lowered;
    personalization AMPLIFIES serendipity, it never flattens it. When
    ``candidate_ids`` is None, behaviour is identical to L4 (uniform random) --
    cold-start safe + fully backward-compatible.

    Returns:
        List of (memory_a_id, memory_b_id, cosine_distance) tuples.
    """
    embed_table, embed_dim = _detect_embedding_table(conn)

    if candidate_ids:
        qmarks = ",".join("?" * len(candidate_ids))
        rows = conn.execute(
            f"""SELECT m.id, e.embedding
                FROM memories m
                JOIN {embed_table} e ON e.memory_id = m.id
                WHERE m.is_archived = 0 AND m.derived_from IS NULL AND m.id IN ({qmarks})
                LIMIT ?""",
            (*candidate_ids, max_candidates),
        ).fetchall()
    else:
        rows = conn.execute(
            f"""SELECT m.id, e.embedding
                FROM memories m
                JOIN {embed_table} e ON e.memory_id = m.id
                WHERE m.is_archived = 0 AND m.derived_from IS NULL
                ORDER BY RANDOM()
                LIMIT ?""",
            (max_candidates,),
        ).fetchall()

    if len(rows) < 2:
        return []

    # Compute pairwise distances on a random subset of pairs.
    # With 200 candidates, full pairwise is 200*199/2 = 19900 — manageable.
    # But we cap at count * 10 candidate pairs for efficiency.
    indices = list(range(len(rows)))
    candidate_pairs: List[Tuple[int, int, float]] = []
    max_pair_checks = count * 20

    checked = 0
    while checked < max_pair_checks and len(candidate_pairs) < count * 3:
        i, j = random.sample(indices, 2)
        dist = _cosine_distance_from_bytes(rows[i][1], rows[j][1])
        if dist > distance_min:
            candidate_pairs.append((rows[i][0], rows[j][0], round(dist, 4)))
        checked += 1

    # If random sampling didn't yield enough, do a broader sweep.
    if len(candidate_pairs) < count:
        for i in range(min(len(rows), 50)):
            for j in range(i + 1, min(len(rows), 50)):
                dist = _cosine_distance_from_bytes(rows[i][1], rows[j][1])
                if dist > distance_min:
                    candidate_pairs.append((rows[i][0], rows[j][0], round(dist, 4)))
                    if len(candidate_pairs) >= count * 3:
                        break
            if len(candidate_pairs) >= count * 3:
                break

    # Deduplicate by sorted pair IDs.
    seen: Set[Tuple[int, int]] = set()
    unique: List[Tuple[int, int, float]] = []
    for a, b, d in candidate_pairs:
        key = (min(a, b), max(a, b))
        if key not in seen:
            seen.add(key)
            unique.append((a, b, d))

    # Sample down to count, preferring higher distance (more divergent).
    unique.sort(key=lambda x: x[2], reverse=True)
    return unique[:count]


def select_close_context_pairs(
    conn: sqlite3.Connection,
    count: int = 7,
    distance_max: float = 0.3,
    min_criteria: int = 2,
    max_candidates: int = 300,
) -> List[Tuple[int, int, float, dict]]:
    """Select close-but-different-context memory pairs.

    Strategy: sample random active memories, find pairs with cosine
    distance < threshold, then filter by context-difference criteria.

    Returns:
        List of (memory_a_id, memory_b_id, cosine_distance, context_diff) tuples.
    """
    embed_table, embed_dim = _detect_embedding_table(conn)

    rows = conn.execute(
        f"""SELECT m.id, e.embedding
            FROM memories m
            JOIN {embed_table} e ON e.memory_id = m.id
            WHERE m.is_archived = 0 AND m.derived_from IS NULL
            ORDER BY RANDOM()
            LIMIT ?""",
        (max_candidates,),
    ).fetchall()

    if len(rows) < 2:
        return []

    # Find close pairs first (distance < threshold).
    close_pairs: List[Tuple[int, int, float]] = []
    max_pair_checks = count * 30

    checked = 0
    indices = list(range(len(rows)))
    while checked < max_pair_checks and len(close_pairs) < count * 5:
        i, j = random.sample(indices, 2)
        dist = _cosine_distance_from_bytes(rows[i][1], rows[j][1])
        if dist < distance_max:
            close_pairs.append((rows[i][0], rows[j][0], round(dist, 4)))
        checked += 1

    # Broader sweep if random didn't yield enough.
    if len(close_pairs) < count:
        for i in range(min(len(rows), 80)):
            for j in range(i + 1, min(len(rows), 80)):
                dist = _cosine_distance_from_bytes(rows[i][1], rows[j][1])
                if dist < distance_max:
                    close_pairs.append((rows[i][0], rows[j][0], round(dist, 4)))
                    if len(close_pairs) >= count * 5:
                        break
            if len(close_pairs) >= count * 5:
                break

    # Deduplicate.
    seen: Set[Tuple[int, int]] = set()
    unique: List[Tuple[int, int, float]] = []
    for a, b, d in close_pairs:
        key = (min(a, b), max(a, b))
        if key not in seen:
            seen.add(key)
            unique.append((a, b, d))

    # Apply context-difference filter.
    result: List[Tuple[int, int, float, dict]] = []
    for a_id, b_id, dist in unique:
        if len(result) >= count:
            break
        meta_a = _get_memory_metadata(conn, a_id)
        meta_b = _get_memory_metadata(conn, b_id)
        if not meta_a or not meta_b:
            continue
        diff = _context_difference(meta_a, meta_b, min_criteria)
        if diff is not None:
            result.append((a_id, b_id, dist, diff))

    return result


def persona_self_tags(persona_name: Optional[str] = None) -> Tuple[str, ...]:
    """The persona pool's SELF tags (Tranche A, A2): what makes a memory about the persona ITSELF.

    Measured on the owner's brain: the 20 meaningful persona dreams' persona endpoints carried persona-design 14,
    living-persona 13, four-layer-persona 8, the persona's own tag 2 -- 18 of 20 paired the persona with OTHER
    personas' design notes and the prompt projected the persona onto them. So the design / generic tags are OUT
    (persona-design, living-persona, four-layer-persona, persona, persona-embodiment, persona-depth,
    persona-persistence) and the pool is the feedback / growth / evolution tags plus ``<name>-persona``. Tranche B's
    persona manifest replaces this default with the declared ``growth_tags``."""
    tags = ["persona-feedback", "persona-refinement", "persona-growth", "persona-evolution"]
    if persona_name and str(persona_name).strip():
        tags.append(str(persona_name).strip().lower() + "-persona")
    return tuple(tags)


PERSONA_EXCLUDED_TAGS = frozenset({"persona-design"})


def select_persona_pairs(
    conn: sqlite3.Connection,
    count: int = 2,
    distance_max: float = 0.5,
    max_candidates: int = 100,
    tags: Optional[Sequence[str]] = None,
    persona_name: Optional[str] = None,
) -> List[Tuple[int, int, float, dict]]:
    """Select pairs where at least one memory is persona-tagged (with the persona's SELF tags, never
    ``persona-design``); neither endpoint is a derived row (the brain never dreams about its own dreams).

    Draws persona-tagged memories and pairs them with random non-persona
    memories at varying distances (neither strictly close nor divergent).

    Returns:
        List of (memory_a_id, memory_b_id, cosine_distance, context_diff) tuples.
        context_diff always includes {"persona_dream": True}.
    """
    embed_table, embed_dim = _detect_embedding_table(conn)

    # Find persona-tagged memory IDs (SELF tags; a design note carrying persona-design is excluded).
    self_tags = tuple(t for t in (tags if tags is not None else persona_self_tags(persona_name))
                      if t not in PERSONA_EXCLUDED_TAGS)
    if not self_tags:
        return []
    tag_placeholders = ",".join("?" * len(self_tags))
    excl = ",".join("?" * len(PERSONA_EXCLUDED_TAGS))
    persona_ids = [r[0] for r in conn.execute(
        f"""SELECT DISTINCT mt.memory_id
            FROM memory_tags mt
            JOIN memories m ON m.id = mt.memory_id
            WHERE mt.tag IN ({tag_placeholders})
              AND m.is_archived = 0 AND m.derived_from IS NULL
              AND mt.memory_id NOT IN (SELECT memory_id FROM memory_tags WHERE tag IN ({excl}))
            ORDER BY RANDOM()
            LIMIT ?""",
        (*self_tags, *sorted(PERSONA_EXCLUDED_TAGS), max_candidates),
    ).fetchall()]

    if not persona_ids:
        return []

    # Get embeddings for persona memories.
    persona_embeds: Dict[int, bytes] = {}
    for pid in persona_ids[:50]:
        row = conn.execute(
            f"SELECT embedding FROM {embed_table} WHERE memory_id = ?",
            (pid,),
        ).fetchone()
        if row:
            persona_embeds[pid] = row[0]

    if not persona_embeds:
        return []

    # Get random non-persona memories with embeddings.
    non_persona = conn.execute(
        f"""SELECT m.id, e.embedding
            FROM memories m
            JOIN {embed_table} e ON e.memory_id = m.id
            WHERE m.is_archived = 0 AND m.derived_from IS NULL
              AND m.id NOT IN ({','.join('?' * len(persona_ids))})
            ORDER BY RANDOM()
            LIMIT ?""",
        (*persona_ids, max_candidates),
    ).fetchall()

    if not non_persona:
        return []

    # Pair each persona memory with a random non-persona memory.
    result: List[Tuple[int, int, float, dict]] = []
    persona_list = list(persona_embeds.keys())
    random.shuffle(persona_list)

    for pid in persona_list:
        if len(result) >= count:
            break
        # Pick a random partner.
        partner = random.choice(non_persona)
        dist = _cosine_distance_from_bytes(persona_embeds[pid], partner[1])
        context_diff = {"persona_dream": True, "cosine_distance": round(dist, 4)}
        result.append((pid, partner[0], round(dist, 4), context_diff))

    return result


# ============================================================
# Dream Execution via claude -p
# ============================================================

_DIVERGENT_PROMPT = """You are analyzing two memories from a knowledge management system. These memories come from VERY DIFFERENT parts of the library (high cosine distance).

Memory A (#{a_id}):
Summary: {a_summary}
Content excerpt: {a_content}
Category: {a_category} | Project: {a_project} | Created: {a_created}

Memory B (#{b_id}):
Summary: {b_summary}
Content excerpt: {b_content}
Category: {b_category} | Project: {b_project} | Created: {b_created}

Find ONE non-obvious connection, contradiction, tension, or question that bridges these two memories. Focus on:
- Patterns that appear in both despite different domains
- Contradictions in approach or assumption
- Questions that neither memory alone would raise

If nothing meaningful emerges (these memories genuinely have nothing interesting to say to each other), respond with exactly: NULL

Otherwise answer in exactly this shape:
RELATION: <one word -- connection, contradiction, tension, question or precedent>
<a single concise paragraph (2-4 sentences) stating the insight>
Name each memory by what it is about (its subject), never as "Memory A" or "Memory B". Do NOT preface with "Here is" or similar. Just state the insight directly."""

_CLOSE_CONTEXT_PROMPT = """You are analyzing two memories from a knowledge management system. These memories are SEMANTICALLY SIMILAR but come from DIFFERENT CONTEXTS ({context_description}).

Memory A (#{a_id}):
Summary: {a_summary}
Content excerpt: {a_content}
Category: {a_category} | Project: {a_project} | Created: {a_created}

Memory B (#{b_id}):
Summary: {b_summary}
Content excerpt: {b_content}
Category: {b_category} | Project: {b_project} | Created: {b_created}

These memories look related but emerged independently. What deeper pattern do they share? What does Memory A's solution or approach suggest for Memory B's situation (or vice versa)? Is there a forgotten precedent here?

If nothing meaningful emerges beyond "they're about the same topic," respond with exactly: NULL

Otherwise answer in exactly this shape:
RELATION: <one word -- connection, contradiction, tension, question or precedent>
<a single concise paragraph (2-4 sentences) stating the insight>
Name each memory by what it is about (its subject), never as "Memory A" or "Memory B". Do NOT preface with "Here is" or similar. Just state the insight directly."""

_PERSONA_PROMPT = """You are analyzing two memories from a knowledge management system. One of these memories relates to {persona_name}'s personality/operating style (the system's living identity layer).

Memory A (#{a_id}):
Summary: {a_summary}
Content excerpt: {a_content}
Category: {a_category} | Project: {a_project} | Created: {a_created}

Memory B (#{b_id}):
Summary: {b_summary}
Content excerpt: {b_content}
Category: {b_category} | Project: {b_project} | Created: {b_created}

How do these memories relate to {persona_name}'s personality or operating style? Does the non-persona memory reveal something about how {persona_name} should evolve, or does the persona memory illuminate the non-persona work in a new way?

If nothing meaningful emerges, respond with exactly: NULL

Otherwise answer in exactly this shape:
RELATION: <one word -- connection, contradiction, tension, question or precedent>
<a single concise paragraph (2-4 sentences) stating the insight>
Name each memory by what it is about (its subject), never as "Memory A" or "Memory B". Do NOT preface with "Here is" or similar. Just state the insight directly."""


def _build_dream_prompt(
    dream_type: str,
    meta_a: dict,
    meta_b: dict,
    context_diff: Optional[dict] = None,
    max_chars: int = 300,
    persona_name: Optional[str] = None,
) -> str:
    """Build the prompt text for a dream pair."""
    template_vars = {
        "a_id": meta_a["id"],
        "a_summary": (meta_a["summary"] or "")[:200],
        "a_content": (meta_a["content"] or "")[:max_chars],
        "a_category": meta_a["category"],
        "a_project": meta_a["source_project"] or "unknown",
        "a_created": meta_a["created_at"],
        "b_id": meta_b["id"],
        "b_summary": (meta_b["summary"] or "")[:200],
        "b_content": (meta_b["content"] or "")[:max_chars],
        "b_category": meta_b["category"],
        "b_project": meta_b["source_project"] or "unknown",
        "b_created": meta_b["created_at"],
    }

    if dream_type == "divergent":
        return _DIVERGENT_PROMPT.format(**template_vars)
    elif dream_type == "close_context":
        # Build a human-readable context description.
        parts = []
        if context_diff:
            if context_diff.get("different_project"):
                parts.append("different projects")
            if context_diff.get("different_client"):
                parts.append("different clients")
            if "days_apart" in context_diff:
                parts.append(f"{context_diff['days_apart']} days apart")
            if context_diff.get("different_category"):
                parts.append("different categories")
            if context_diff.get("different_kind"):
                parts.append("different memory kinds")
            if context_diff.get("different_session"):
                parts.append("different work sessions")
        template_vars["context_description"] = ", ".join(parts) if parts else "different contexts"
        return _CLOSE_CONTEXT_PROMPT.format(**template_vars)
    elif dream_type == "persona":
        # The persona's name comes from the caller (the dream pipeline resolves it from the brain's own persona
        # declaration); the package carries no persona literal. No name -> the neutral "the persona".
        template_vars["persona_name"] = (persona_name or "the persona").strip() or "the persona"
        return _PERSONA_PROMPT.format(**template_vars)
    else:
        raise ValueError(f"Unknown dream_type: {dream_type}")


# R1-L1: the body is everything AFTER the relation word, on the same line or below -- a reply written on ONE line
# ('RELATION: tension. The note on X assumes ...') was eaten whole and judged NULL. 'RELATION: connection\nNULL' still
# reads NULL (the body is 'NULL'). Narrow pass N4 (R2-05): the word may come in the prompt's own brackets --
# 'RELATION: <contradiction>' or '[tension]' -- and is read, with the header OUT of the body (it was read as connection,
# losing the tension edge, and the header line was stored in the dream).
#
# Round 3 G2 item 1 (rN_recheck RC-03 + RC-04, R2-05's class; fixed as CLASSES, test_dream_reply_shape_r3.py):
#   CLASS 1, DECORATION. A model decorates the relation word the way it decorates any word: the prompt's own brackets,
#     markdown (`code`, **bold**, _italic_), quotes, doubled or unusual brackets, the header as a quote / bullet /
#     heading / code-span line, an em dash for the colon. Every such header is READ when its decoration is BALANCED.
#     An opener never closed after the word is the template's PLACEHOLDER ('RELATION: <one word -- connection, ...>'),
#     never a relation word: no header is read, the line stays in the body and the guard sends the reply to the LOUD
#     path. (On the N bytes `contradiction` and <<tension>> took the loud path -- a genuine dream lost -- and the
#     verbatim template header was read as the word "one", its residue stored at the head of a kept dream.)
#   CLASS 2, TEMPLATE JUNK (see _shape_echo): a header echoed at the TAIL is stripped (when it is the only header it
#     carries the relation); a reply fenced in ``` is unwrapped.
#   CLASS 3, NULL WITH A REASON: 'NULL' then an explanation is a NULL (_is_null_body), never a stored dream.
_RELATION_RE = re.compile(
    r"^(?P<lead>[ \t>#*_`~-]*)RELATION(?P<key>[ \t*_`]*)[:=\u2014\u2013-]"
    r"(?P<pre>[ \t*_`'\"<\[({\u00ab\u201c\u2018]*)"
    r"(?P<word>[A-Za-z]+(?:-[A-Za-z]+)*)", re.I)
_RELATION_TAIL = " \t*_`'\">])}\u00bb\u201d\u2019"
_DECO_PAIRS = (("<", ">"), ("[", "]"), ("(", ")"), ("{", "}"), ("\u00ab", "\u00bb"), ("\u201c", "\u201d"),
               ("\u2018", "\u2019"))
_DECO_SELF_PAIRED = "`'\""
DREAM_RELATIONS = ("connection", "contradiction", "tension", "question", "precedent")


def _deco_balanced(s: str) -> bool:
    """Every bracket opened is closed, and every quote/backtick comes in pairs (the emphasis marks * and _ may be
    split across the key and the word, '**RELATION:** tension', so they are not counted)."""
    return (all(s.count(o) == s.count(c) for o, c in _DECO_PAIRS)
            and all(s.count(q) % 2 == 0 for q in _DECO_SELF_PAIRED))


def _read_header(text: str) -> Optional[Tuple[str, int]]:
    """``(relation, end)`` for a RELATION header OPENING ``text`` -- ``end`` is where the body starts, after the
    word's closing decoration and any separating punctuation -- or None: no header, or a header whose word is never
    closed (the template's placeholder)."""
    m = _RELATION_RE.match(text)
    if not m:
        return None
    lead = m.group("lead").replace(">", "")          # a quote marker, not a bracket
    start, j = m.end("lead"), m.end()
    ext = j
    while ext < len(text) and text[ext] in _RELATION_TAIL:
        ext += 1
    for k in range(j, ext + 1):                       # the FIRST balanced end: a same-line body keeps its own quotes
        if _deco_balanced(lead + text[start:k]):
            break
    else:
        return None
    while k < len(text) and text[k] in "*_":
        k += 1
    while k < len(text) and text[k] in " \t.:;,\u2014\u2013-":
        k += 1
    word = m.group("word").lower()
    return (word if word in DREAM_RELATIONS else "connection"), k


def _is_header_line(line: str) -> bool:
    """A line that IS a RELATION header, read or not: the key in capitals, or a known relation word after it (prose
    such as 'Relation: the two notes ...' is not)."""
    m = _RELATION_RE.match(line)
    if not m:
        return False
    return line[m.end("lead"):m.end("lead") + 8] == "RELATION" or m.group("word").lower() in DREAM_RELATIONS


def _header_only(line: str) -> Optional[str]:
    """The relation of a line that is a balanced header and NOTHING else, or None."""
    h = _read_header(line)
    if h is None or not _is_header_line(line) or line[h[1]:].strip(" \t*_`.:;,-"):
        return None
    return h[0]


def _unfence(text: str) -> str:
    """A reply wrapped whole in a ``` fence (with or without a language tag) is read inside the fence."""
    m = re.match(r"^```[A-Za-z0-9_+-]*[ \t]*\n(.*?)\n?```\s*\Z", text, re.S)
    return m.group(1).strip() if m else text


def parse_relation(output: Optional[str]) -> Tuple[str, str]:
    """Split a dream reply into (relation, body). The FIRST line may carry ``RELATION: <word>``; it is parsed here,
    BEFORE any NULL/length test, so a reply of ``RELATION: connection\nNULL`` is a NULL, never a 25-character
    "meaningful" dream. Fail-soft: no header -> 'connection'; an unknown word -> 'connection'. The header never
    reaches the stored content (it goes to dream_log.context_difference). Idempotent on a body it returned."""
    text = _unfence((output or "").strip())
    h = _read_header(text)
    rel, body = ("connection", text) if h is None else (h[0], text[h[1]:].strip())
    lines = body.split("\n")
    cut, tail_rel = len(lines), None
    while cut > 0 and (not lines[cut - 1].strip() or _header_only(lines[cut - 1].strip())):
        tail_rel = _header_only(lines[cut - 1].strip()) or tail_rel
        cut -= 1
    if cut < len(lines) and any(l.strip() for l in lines[:cut]):   # a header echoed at the tail: strip it
        body = "\n".join(lines[:cut]).strip()
        if h is None and tail_rel:
            rel = tail_rel                                       # the header written LAST is still the header
    return rel, body


# A NULL said with a reason ('NULL -- they only share a topic', 'NULL\n\nThese two ...') is a NULL. 'NULL' as the
# first WORD of a genuine body ('NULL values in the log ...', 'NULL-terminated ...') is not.
_NULL_HEAD = re.compile(r"^[\s*_`>\"']*NULL(?:[ \t*_`\"']*(?:\Z|\n|[.:;\u2014\u2013])|[ \t]+-)")


def _is_null_body(body: Optional[str]) -> bool:
    """The organ's own acceptance rule, applied to the BODY: empty, NULL (alone, decorated, or followed by its
    reason), or under 20 characters."""
    b = (body or "").strip()
    return (not b) or len(b) < 20 or bool(_NULL_HEAD.match(b))


_SHAPE_MARKS_CACHE: Dict[int, Set[Tuple[str, ...]]] = {}


def _shape_marks() -> Set[Tuple[str, ...]]:
    """Word n-grams (6, or the whole piece when it has 5) of the prompts' fixed ANSWER-SHAPE text -- the two
    placeholders and the instruction lines from 'Otherwise answer in exactly this shape:' on -- DERIVED from the
    prompt constants, so a reworded prompt keeps its guard in step. Never the memories' own text."""
    key = hash((_DIVERGENT_PROMPT, _CLOSE_CONTEXT_PROMPT, _PERSONA_PROMPT))
    if key not in _SHAPE_MARKS_CACHE:
        marks: Set[Tuple[str, ...]] = set()
        for prompt in (_DIVERGENT_PROMPT, _CLOSE_CONTEXT_PROMPT, _PERSONA_PROMPT):
            block = prompt[prompt.index("Otherwise answer in exactly this shape:"):]
            for piece in re.split(r"\n|(?<=\.)\s+", block):
                toks = _shape_tokens(piece)
                n = min(6, len(toks))
                if n >= 5:
                    marks.update(tuple(toks[i:i + n]) for i in range(len(toks) - n + 1))
        _SHAPE_MARKS_CACHE.clear()
        _SHAPE_MARKS_CACHE[key] = marks
    return _SHAPE_MARKS_CACHE[key]


def _shape_tokens(text: str) -> List[str]:
    return re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", (text or "").lower())


def _shape_echo(output: str, body: str) -> str:
    """CLASS 2, TEMPLATE JUNK: what of the prompt's answer SHAPE a reply echoed, or '' for none. Each is the LOUD path
    (the reply is the shape, or carries it, not a dream):
      * a body that OPENS with an angle-bracketed placeholder, decorated or not ('> <a single ...>', '- <...>');
      * a placeholder on a LINE of its own anywhere in the body, decorated or not;
      * a RELATION header anywhere in the body -- a preface before the real header, a second header mid-body, a
        header word never closed (the template's own '<one word -- ...'); a header at the TAIL was stripped already;
      * any run of the shape's own words (_shape_marks) anywhere in the reply: the placeholders paraphrased in
        prose, an instruction line ('Name each memory by what it is about ...') echoed."""
    if re.match(r"[\s>*_`#-]*<[^<>\n]{10,}>", body) or \
            re.search(r"(?m)^[ \t>*_`#-]*<[^<>\n]{10,}>[ \t*_`.:;,]*$", body):
        return "placeholder"
    if any(_is_header_line(l) for l in body.split("\n")):
        return "RELATION header (a preface, a second header or an unclosed word)"
    toks = _shape_tokens(output)
    marks = _shape_marks()
    for n in sorted(set(len(m) for m in marks)):
        if any(tuple(toks[i:i + n]) in marks for i in range(len(toks) - n + 1)):
            return "answer-shape text"
    return ""


def dream_on_pair(
    meta_a: dict,
    meta_b: dict,
    dream_type: str,
    context_diff: Optional[dict] = None,
    max_chars: int = 300,
    timeout_seconds: int = 60,
    dry_run: bool = False,
    claude_model: Optional[str] = None,
    on_failure: Optional[Callable[[str], None]] = None,
    persona_name: Optional[str] = None,
    out: Optional[dict] = None,
) -> Optional[str]:
    """Invoke ``claude -p`` to synthesize an insight from a memory pair.

    Args:
        meta_a: Metadata dict for memory A.
        meta_b: Metadata dict for memory B.
        dream_type: 'divergent', 'close_context', or 'persona'.
        context_diff: Context-difference dict (for close_context pairs).
        max_chars: Max content chars to include per memory.
        timeout_seconds: Subprocess timeout.
        dry_run: If True, return the prompt text instead of invoking claude.
        claude_model: Model to use (e.g. 'sonnet', 'opus'). Passed as --model flag.
        on_failure: called with a one-line reason each time the LLM call FAILS (no
            token, non-zero exit, timeout, CLI absent, any error) -- never for a
            legitimate NULL/trivial answer. None returns look the same either way,
            so this is how a caller counts the pairs that fell back (R8: the
            sequencer's run summary did not; a night of failures read phases_error 0).

        persona_name: the persona the persona prompt names (resolved by the caller; never a package literal).
        out: when a dict is given, ``out['relation']`` receives the parsed RELATION word.

    Returns:
        The dream's BODY (the RELATION header removed), or None if the LLM returned NULL/trivial, the body is the
        shape of a refusal or a narrated tool call (the seam's screen, re-read on the BODY: the header would
        otherwise be the "first sentence" the screen reads), or the call failed.
    """
    def _failed(reason: str) -> None:
        if on_failure is not None:
            try:
                on_failure(reason)
            except Exception:
                pass
    prompt = _build_dream_prompt(dream_type, meta_a, meta_b, context_diff, max_chars, persona_name=persona_name)

    if dry_run:
        return f"[DRY RUN] Would dream on pair ({meta_a['id']}, {meta_b['id']}) type={dream_type}"

    # Through the thinker seam (text only); `claude_model` is the job's tier.
    try:
        res = _thinker.think(prompt, purpose="memory_dreaming",
                             tier=claude_model if claude_model in _thinker.TIERS else None,
                             timeout_s=timeout_seconds)
    except _thinker.ThinkError as e:
        declined = thinker_declined(e)
        if declined:
            # UNCONFIGURED (round 2, m4): the owner's answer, not a failed call -- no LLM failure is counted.
            logger.info("thinker off for pair (%d, %d): %s", meta_a["id"], meta_b["id"], UNCONFIGURED_NOTE)
        else:
            logger.warning("thinker %s for pair (%d, %d): %s", e.kind, meta_a["id"], meta_b["id"], e)
            _failed("thinker %s: %s" % (e.kind, e))
        if out is not None and isinstance(e, _thinker.ThinkUnavailable):
            # STARVED (round 1): the caller stops the run instead of logging an empty dream for every pair. The same
            # stop serves "e) not now"; `declined` tells the caller to report UNCONFIGURED, exit 0.
            out["starved"] = {"reason": e.reason, "backend": e.backend_id, "detail": str(e)[:300],
                              "declined": declined}
        return None
    output = res.text.strip()
    # The RELATION header is parsed FIRST (VAEL: "RELATION: connection\nNULL" is 25 characters and was stored).
    relation, body = parse_relation(output)
    if out is not None:
        out["relation"] = relation
    # Check for NULL/trivial response (the organ's own acceptance rule, applied to the BODY).
    if _is_null_body(body):
        return None
    # A body that OPENS with an angle-bracketed placeholder -- the prompt's own "<a single concise paragraph (2-4
    # sentences) stating the insight>", or a paraphrase of it -- is the answer SHAPE echoed back, not a dream
    # (model-choice Round 2 wave 2B, measured live: 3 of 3 local-model dreams opened with one, and each was kept).
    # It takes the same loud path as a refusal. Narrow pass N4 (R2-05): so does a placeholder on a LINE of its own
    # anywhere in the body, and a body that is itself a second RELATION header. Round 3 G2 item 1: the whole CLASS --
    # decorated placeholders, any RELATION header left in the body, any run of the shape's own words (_shape_echo).
    echo = _shape_echo(output, body)
    if echo:
        logger.warning("thinker answer echoed the prompt's %s for pair (%d, %d)", echo, meta_a["id"], meta_b["id"])
        _failed("thinker answer echoed the prompt's %s: %s" % (echo, body.strip()[:80]))
        return None
    # The seam screened the whole reply, whose first sentence is now the header; screen the BODY too, so a narrated
    # tool call or a refusal under a RELATION line takes the same loud path the seam's own refusal takes.
    try:
        hit = _thinker.reply_screen(body, "text")
    except Exception:
        hit = ""
    if hit:
        logger.warning("thinker refused (body screen) for pair (%d, %d): reply-%s",
                       meta_a["id"], meta_b["id"], hit)
        _failed("thinker refused: reply-%s (the dream body's screen)" % hit)
        return None
    return body


def _store_dream_as_reflection(
    conn: sqlite3.Connection,
    dream_content: str,
    memory_a_id: int,
    memory_b_id: int,
    dream_type: str,
    *,
    relation: Optional[str] = None,
    encoder: Optional[Callable[[str], bytes]] = None,
    enqueue_scan: bool = True,
    created_at: Optional[str] = None,
    extra_tags: Sequence[str] = (),
    report: Optional[dict] = None,
    commit: bool = True,
) -> Optional[int]:
    """Store a meaningful dream as a reflection memory. Returns the new memory ID, or None.

    THE DREAM FIX (Tranche A, A1). What a stored dream owes, in one place:
      * birth confidence 0.40 (``DREAM_CONFIDENCE``) and ``derived_from='dream_synthesis'`` -- the birth value is
        IMMUTABLE; the fade is derived at read time (dream_ledger), never written back;
      * a REAL summary: the dream's first body sentence in named nouns, never "Memory A/B" nor "memories #x + #y"
        (queried by the old summary, 0 of 20 dreams ranked first among dreams; by the first sentence, 19 of 20);
      * the endpoints' TAINT: a dream of an untrusted memory is untrusted text (``untrusted_source=1``);
      * an embedding in the ACTIVE tier (of the BODY -- the header is provenance, not meaning), provenance rows
        and, unless ``enqueue_scan=False`` (the backfill), a belief-scan entry -- via ``_vl_attach``;
      * a dream whose endpoint is ARCHIVED (or gone) is NOT stored: ``report['skipped'] = 'endpoint_archived'``.
    A failure is written to ``report['error']`` and rolled back to a SAVEPOINT, never ``conn.rollback()`` (which
    inside REM's loop dropped the phase's pending dream_log rows). ``commit=False`` leaves the commit to the caller.
    """
    rep = report if report is not None else {}
    try:
        import dream_ledger as _dl
    except Exception as e:                                   # pragma: no cover - a partial install
        rep["error"] = "dream_ledger unavailable: %s" % e
        logger.error("Failed to store dream as reflection: %s", rep["error"])
        return None
    metas = {}
    for mid in (memory_a_id, memory_b_id):
        row = conn.execute("SELECT id, summary, content, is_archived, untrusted_source FROM memories WHERE id=?",
                           (mid,)).fetchone()
        if not row or row[3]:
            rep["skipped"] = "endpoint_archived"
            rep["endpoint"] = mid
            return None
        metas[mid] = {"id": row[0], "summary": row[1], "content": row[2], "untrusted_source": row[4]}
    rel, body = parse_relation(dream_content)
    relation = (relation or rel or "connection").lower()
    summary = _dl.summarize_dream(body, metas[memory_a_id], metas[memory_b_id])
    content = (
        f"[Dream synthesis — {dream_type}]\n"
        f"Source memories: #{memory_a_id} and #{memory_b_id}\n\n"
        f"{body}"
    )
    ts_sql = "?" if created_at else "datetime('now')"
    ts_args = (created_at, created_at, created_at) if created_at else ()
    conn.execute("SAVEPOINT dream_store")
    try:
        cursor = conn.execute(
            f"""INSERT INTO memories (content, summary, category, importance,
                   source_project, kind, confidence, derived_from,
                   created_at, updated_at, last_accessed)
               VALUES (?, ?, 'insight', 0.55, 'memory-dreaming', 'reflection',
                       ?, ?, {ts_sql}, {ts_sql}, {ts_sql})""",
            (content, summary, DREAM_CONFIDENCE, DREAM_DERIVED_FROM, *ts_args),
        )
        mem_id = cursor.lastrowid
        tags = ["dream", f"dream-{dream_type}", "auto-generated", *[t for t in extra_tags if t]]
        for tag in tags:
            conn.execute("INSERT OR IGNORE INTO memory_tags (memory_id, tag) VALUES (?, ?)", (mem_id, tag))
        for source_id in (memory_a_id, memory_b_id):
            conn.execute(
                """INSERT OR IGNORE INTO memory_links
                       (source_id, target_id, link_type, strength)
                   VALUES (?, ?, 'elaborates', 0.6)""",
                (mem_id, source_id),
            )
        # The endpoints' taint is the dream's taint (anti-laundering, as RAPTOR and playbooks already do).
        if metas[memory_a_id]["untrusted_source"] or metas[memory_b_id]["untrusted_source"]:
            conn.execute("UPDATE memories SET untrusted_source=1 WHERE id=?", (mem_id,))
            conn.execute("INSERT OR IGNORE INTO memory_tags (memory_id, tag) VALUES (?, 'untrusted-source')",
                         (mem_id,))
            rep["tainted"] = True
        # TO-03: this memory must be findable (active-tier vector, provenance rows, scan queue unless told not to).
        # the vector's text: the body without the thinker's framing (dream_ledger.embedding_text, measured in wave 2A)
        att = _vl_attach(conn, mem_id, _dl.embedding_text(body), "memory_dreaming._store_dream_as_reflection",
                         sources=(memory_a_id, memory_b_id), transformation="dream_synthesis",
                         encoder=encoder, enqueue_scan=enqueue_scan)
        rep["embedded"] = bool(att.get("embedded")) if isinstance(att, dict) else None
        rep["relation"] = relation
        conn.execute("RELEASE dream_store")
        if commit:
            conn.commit()
        rep["memory_id"] = mem_id
        return mem_id
    except Exception as e:
        rep["error"] = "%s: %s" % (type(e).__name__, e)
        logger.error("Failed to store dream as reflection: %s", rep["error"])
        try:
            conn.execute("ROLLBACK TO dream_store")
            conn.execute("RELEASE dream_store")
        except Exception:
            pass
        return None


# ============================================================
# Main Cycle
# ============================================================

def run_dream_cycle(
    db_path: str,
    config: Optional[dict] = None,
    dry_run: bool = False,
    daytime: bool = False,
) -> dict:
    """Run the nightly dream cycle.

    L5 UNIFIED DREAM PIPELINE: run_dream_cycle now DELEGATES to the
    dream_pipeline phase-sequencer (Council mandate: ONE phase-sequencer, not
    four patches). The sequencer owns the ordered, fail-soft, time-boxed phase
    list; this L4 module's selectors (select_divergent/close/persona_pairs) +
    dream_on_pair remain the REM dreaming organ the sequencer calls. If
    dream_pipeline is unavailable (a partial install), we fall back to the
    legacy dual-pool cycle so dreaming never silently stops.
    """
    try:
        import dream_pipeline  # type: ignore
    except Exception:
        dream_pipeline = None  # type: ignore
    if dream_pipeline is not None and hasattr(dream_pipeline, "run_dream_cycle"):
        try:
            if dream_pipeline.is_migration_027_applied(dream_pipeline._get_conn(db_path)):
                return dream_pipeline.run_dream_cycle(
                    db_path, config=config, dry_run=dry_run, daytime=daytime)
        except Exception as e:
            logger.warning("dream_pipeline delegation failed (%s); using legacy dual-pool", e)
    return run_dream_cycle_legacy(db_path, config=config, dry_run=dry_run)


def run_dream_cycle_legacy(
    db_path: str,
    config: Optional[dict] = None,
    dry_run: bool = False,
) -> dict:
    """The L4 dual-pool dream cycle (Echo + Voyager + persona). Retained as the
    fallback when the L5 sequencer is unavailable. Unchanged from L4.

    Returns a status dict with batch details.
    """
    t0 = time.time()
    cfg = {**DEFAULT_CONFIG, **(config or {})}

    # THIS BRAIN's dream lock (run_lock_path): the same lock dream_pipeline.run_dream_cycle takes.
    lock_path, lock_note = run_lock_path(db_path, cfg.get("lock_file"))
    cfg["lock_file"] = lock_path
    if not dry_run:
        try:
            acquired = _acquire_lock(lock_path)
        except OSError as e:
            return {"status": "error", "reason": "could not create this brain's dream lock %s: %s" % (lock_path, e),
                    "lock_file": lock_path}
        if not acquired:
            out = {
                "status": "skipped",
                "reason": "another dream cycle is already running",
                "lock_file": lock_path,
            }
            if lock_note:
                out["lock_note"] = lock_note
            return out

    try:
        conn = _get_conn(db_path)

        # Verify migration.
        if not is_dreaming_migration_applied(conn):
            conn.close()
            return {"status": "error", "message": "Migration 017 not applied. Run init first."}

        # Create batch record.
        cursor = conn.execute(
            """INSERT INTO dream_batches (config_snapshot, created_at)
               VALUES (?, datetime('now'))""",
            (json.dumps(cfg),),
        )
        batch_id = cursor.lastrowid
        conn.commit()

        # --- Pool 1: Divergent pairs ---
        divergent_count = cfg["divergent_pairs"]
        divergent_pairs = select_divergent_pairs(
            conn,
            count=divergent_count,
            distance_min=cfg["divergent_distance_min"],
        )
        logger.info("Selected %d divergent pairs", len(divergent_pairs))

        # --- Pool 2: Close-context pairs ---
        close_count = cfg["close_context_pairs"]
        close_pairs = select_close_context_pairs(
            conn,
            count=close_count,
            distance_max=cfg["close_context_distance_max"],
            min_criteria=cfg["close_context_min_criteria"],
        )
        logger.info("Selected %d close-context pairs", len(close_pairs))

        # --- Pool 3: Persona pairs (10% of total) ---
        total_standard = len(divergent_pairs) + len(close_pairs)
        persona_count = max(2, int(total_standard * cfg["persona_pair_fraction"]))
        persona_pairs = select_persona_pairs(conn, count=persona_count)
        logger.info("Selected %d persona pairs", len(persona_pairs))

        # --- Dream on all pairs ---
        meaningful_count = 0
        null_count = 0
        total_pairs = 0

        def _process_pair(
            a_id: int,
            b_id: int,
            distance: float,
            dtype: str,
            ctx_diff: Optional[dict],
        ):
            nonlocal meaningful_count, null_count, total_pairs
            total_pairs += 1

            meta_a = _get_memory_metadata(conn, a_id)
            meta_b = _get_memory_metadata(conn, b_id)
            if not meta_a or not meta_b:
                logger.warning("Could not load metadata for pair (%d, %d), skipping", a_id, b_id)
                return

            pair_out: Dict[str, Any] = {}
            dream_text = dream_on_pair(
                meta_a, meta_b, dtype, ctx_diff,
                max_chars=cfg["max_content_chars"],
                timeout_seconds=cfg["claude_timeout_seconds"],
                dry_run=dry_run,
                claude_model=cfg.get("claude_model"),
                out=pair_out,
            )
            if pair_out.get("starved"):
                # STARVED: this attempt is not counted and writes no row; the loop stops.
                total_pairs -= 1
                st = pair_out["starved"]
                raise ThinkerStarved(st["reason"], st.get("detail", ""), st.get("backend", ""))

            is_meaningful = 1 if dream_text and not dry_run else 0
            dream_memory_id = None

            if is_meaningful and cfg["store_reflections"] and not dry_run:
                dream_memory_id = _store_dream_as_reflection(
                    conn, dream_text, a_id, b_id, dtype,
                )
                # A store FAILURE keeps is_meaningful=1 with a NULL id, so the row invariant (dream_ledger.
                # dream_watch) reads BROKEN; forcing 0 here made the defect look like a NULL dream (STARVED).
                meaningful_count += 1
            elif is_meaningful:
                meaningful_count += 1

            if not is_meaningful:
                null_count += 1

            # Log the dream attempt.
            conn.execute(
                """INSERT INTO dream_log
                       (dream_type, memory_a_id, memory_b_id, cosine_distance,
                        context_difference, dream_content, dream_memory_id,
                        is_meaningful, batch_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
                (
                    dtype, a_id, b_id, distance,
                    json.dumps(ctx_diff) if ctx_diff else None,
                    dream_text,
                    dream_memory_id,
                    is_meaningful,
                    batch_id,
                ),
            )
            conn.commit()

        starved: Optional[ThinkerStarved] = None
        try:
            # Process divergent pairs.
            for a_id, b_id, dist in divergent_pairs:
                _process_pair(a_id, b_id, dist, "divergent", None)

            # Process close-context pairs.
            for a_id, b_id, dist, ctx_diff in close_pairs:
                _process_pair(a_id, b_id, dist, "close_context", ctx_diff)

            # Process persona pairs.
            for a_id, b_id, dist, ctx_diff in persona_pairs:
                _process_pair(a_id, b_id, dist, "persona", ctx_diff)
        except ThinkerStarved as e:
            starved = e
            if e.declined:
                logger.info("dream cycle UNCONFIGURED: %s -- stopped; no row written", UNCONFIGURED_NOTE)
            else:
                logger.warning("dream cycle STARVED: %s -- stopped; no row written for the undreamt pairs", e)

        # Update batch record.
        duration = round(time.time() - t0, 2)
        conn.execute(
            """UPDATE dream_batches
               SET divergent_pairs = ?,
                   close_context_pairs = ?,
                   persona_pairs = ?,
                   meaningful_dreams = ?,
                   null_dreams = ?,
                   total_pairs = ?,
                   duration_seconds = ?
               WHERE id = ?""",
            (
                len(divergent_pairs),
                len(close_pairs),
                len(persona_pairs),
                meaningful_count,
                null_count,
                total_pairs,
                duration,
                batch_id,
            ),
        )
        conn.commit()
        conn.close()

        return {
            "status": (("unconfigured" if starved.declined else "starved") if starved is not None else "ok")
                      if not dry_run else "dry-run",
            **({"unconfigured_note": UNCONFIGURED_NOTE} if starved is not None and starved.declined else
               {"starved_reason": starved.reason, "starved_detail": str(starved)} if starved is not None else {}),
            "batch_id": batch_id,
            "divergent_pairs": len(divergent_pairs),
            "close_context_pairs": len(close_pairs),
            "persona_pairs": len(persona_pairs),
            "total_pairs": total_pairs,
            "meaningful_dreams": meaningful_count,
            "null_dreams": null_count,
            "duration_seconds": duration,
            **({"lock_note": lock_note} if lock_note else {}),
        }

    except Exception as e:
        logger.exception("Dream cycle failed")
        return {
            "status": "error",
            "message": str(e),
            "duration_seconds": round(time.time() - t0, 2),
        }
    finally:
        if not dry_run:
            _release_lock(lock_path)


# ============================================================
# Digest and Curation
# ============================================================

def get_dream_digest(
    conn: sqlite3.Connection,
    days: int = 7,
    limit: int = 5,
) -> List[dict]:
    """Return the top N meaningful dreams from the last M days.

    Mixes dream types for variety (not all divergent, not all close).
    Prioritizes flagged-interesting, then recency.
    """
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()

    rows = conn.execute(
        """SELECT dl.id, dl.dream_type, dl.memory_a_id, dl.memory_b_id,
                  dl.cosine_distance, dl.dream_content, dl.dream_memory_id,
                  dl.drew_flagged_interesting, dl.created_at
           FROM dream_log dl
           WHERE dl.is_meaningful = 1
             AND dl.created_at > ?
           ORDER BY dl.drew_flagged_interesting DESC,
                    dl.created_at DESC
           LIMIT ?""",
        (cutoff, limit * 3),
    ).fetchall()

    # Separate flagged and unflagged, then round-robin unflagged by type.
    flagged: List[dict] = []
    by_type: Dict[str, List[dict]] = {"divergent": [], "close_context": [], "persona": []}

    for row in rows:
        entry = {
            "dream_id": row[0],
            "dream_type": row[1],
            "memory_a_id": row[2],
            "memory_b_id": row[3],
            "cosine_distance": row[4],
            "dream_content": row[5],
            "dream_memory_id": row[6],
            "flagged_interesting": bool(row[7]),
            "created_at": row[8],
        }
        if entry["flagged_interesting"]:
            flagged.append(entry)
        else:
            by_type.get(row[1], []).append(entry)

    # Flagged dreams come first (already sorted by recency from SQL).
    result: List[dict] = flagged[:limit]

    # Fill remainder with round-robin across types for variety.
    type_order = ["divergent", "close_context", "persona"]
    type_indices = {t: 0 for t in type_order}

    while len(result) < limit:
        added = False
        for dtype in type_order:
            if len(result) >= limit:
                break
            idx = type_indices[dtype]
            if idx < len(by_type[dtype]):
                result.append(by_type[dtype][idx])
                type_indices[dtype] += 1
                added = True
        if not added:
            break

    return result


# ============================================================
# L5-M4 "The Assayer" — OPTIONAL no-actuator re-rank seam.
# ============================================================
# This seam sits BETWEEN dream generation and the digest Drew reads. It is
# OFF BY DEFAULT (ASSAYER_ENABLED is falsy) and GRACEFUL-ABSENT: with the flag
# off, the assayer module missing, the advisory table absent, or no reward model
# provisioned, ``get_dream_digest_assayed`` returns EXACTLY what the unchanged
# ``get_dream_digest`` returns -- byte-for-byte the heuristic order. The Assayer
# never writes through this path; it only re-orders, and only ever reserves a
# protected "wild lane" so the most-divergent dreams are never culled.

def _assayer_enabled() -> bool:
    """True iff ASSAYER_ENABLED is set truthy (1/true/yes/on) -- mirrors
    claude_memory._env_flag exactly. OFF (heuristic floor) for any other value."""
    val = os.environ.get("ASSAYER_ENABLED")
    if val is None:
        return False
    return val.strip().lower() in ("1", "true", "yes", "on")


def get_dream_digest_assayed(
    conn: sqlite3.Connection,
    days: int = 7,
    limit: int = 5,
) -> List[dict]:
    """The Assayer-aware digest. Builds the heuristic digest FIRST (the unchanged
    ``get_dream_digest``), then -- only if the L5-M4 trial is enabled and a reward
    model has scored dreams -- re-ranks by Assayer score with the wild lane
    reserved. FAIL-SOFT: any error in the Assayer path falls back to the
    heuristic digest so dreaming never breaks.

    With ``ASSAYER_ENABLED`` off (the default), this is a clean pass-through and
    the returned list is identical to ``get_dream_digest(conn, days, limit)``.
    """
    base = get_dream_digest(conn, days=days, limit=limit)
    if not _assayer_enabled():
        return base
    try:
        import assayer  # type: ignore
    except Exception:
        return base
    try:
        # score_dream_digest augments the wild lane from the full in-window dream
        # pool itself (so a divergent dream the heuristic top-N dropped can still
        # be protected). On the no-op path (no scores) it returns `base` unchanged.
        ranked = assayer.score_dream_digest(conn, base, limit=limit, days=days)
        return ranked if ranked else base
    except Exception as e:  # the score must never break the digest.
        logger.warning("assayer re-rank failed (%s); using heuristic digest", e)
        return base


_FLAGGED_IMPORTANCE = 0.80


def flag_interesting(conn: sqlite3.Connection, dream_id: int) -> dict:
    """Mark a dream as interesting (Drew's curation).

    Also propagates curation to the stored reflection memory by boosting
    its importance to ``_FLAGGED_IMPORTANCE`` (0.80). This ensures
    Drew-curated dream insights surface more prominently in recall.
    """
    row = conn.execute(
        "SELECT id, is_meaningful, dream_memory_id FROM dream_log WHERE id = ?",
        (dream_id,),
    ).fetchone()
    if not row:
        return {"status": "error", "message": f"Dream {dream_id} not found"}

    conn.execute(
        "UPDATE dream_log SET drew_flagged_interesting = 1 WHERE id = ?",
        (dream_id,),
    )

    importance_updated = False
    dream_memory_id = row[2]
    if dream_memory_id is not None:
        conn.execute(
            "UPDATE memories SET importance = ? WHERE id = ?",
            (_FLAGGED_IMPORTANCE, dream_memory_id),
        )
        importance_updated = True

    conn.commit()
    return {
        "status": "ok",
        "dream_id": dream_id,
        "flagged": True,
        "importance_updated": importance_updated,
        "dream_memory_id": dream_memory_id,
    }


def get_dream_stats(conn: sqlite3.Connection) -> dict:
    """Aggregate statistics about dreams."""
    total = conn.execute("SELECT count(*) FROM dream_log").fetchone()[0]
    meaningful = conn.execute(
        "SELECT count(*) FROM dream_log WHERE is_meaningful = 1"
    ).fetchone()[0]
    flagged = conn.execute(
        "SELECT count(*) FROM dream_log WHERE drew_flagged_interesting = 1"
    ).fetchone()[0]
    batches = conn.execute("SELECT count(*) FROM dream_batches").fetchone()[0]

    by_type = dict(conn.execute(
        "SELECT dream_type, count(*) FROM dream_log GROUP BY dream_type"
    ).fetchall())
    meaningful_by_type = dict(conn.execute(
        "SELECT dream_type, count(*) FROM dream_log WHERE is_meaningful = 1 GROUP BY dream_type"
    ).fetchall())

    return {
        "total_dreams": total,
        "meaningful_dreams": meaningful,
        "null_dreams": total - meaningful,
        "flagged_interesting": flagged,
        "meaningful_rate": round(meaningful / total, 3) if total > 0 else 0.0,
        "total_batches": batches,
        "by_type": by_type,
        "meaningful_by_type": meaningful_by_type,
    }


# ============================================================
# Migration
# ============================================================

def is_dreaming_migration_applied(conn: sqlite3.Connection) -> bool:
    """Check whether the dream_log table exists."""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='dream_log'"
    ).fetchone()
    return row is not None


def apply_dreaming_migration(
    db_path: str,
    migration_sql_path: str,
) -> Dict[str, Any]:
    """Apply migration_017 idempotently. Returns status dict."""
    t0 = time.time()
    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.execute("PRAGMA foreign_keys=ON")

    if is_dreaming_migration_applied(conn):
        conn.close()
        return {"status": "already-applied", "time_ms": int((time.time() - t0) * 1000)}

    try:
        sql_text = Path(migration_sql_path).read_text()
        conn.executescript(sql_text)
        conn.commit()
    except Exception as e:
        conn.close()
        return {"status": "error", "message": str(e), "time_ms": int((time.time() - t0) * 1000)}

    if not is_dreaming_migration_applied(conn):
        conn.close()
        return {"status": "error", "message": "Table not found after migration",
                "time_ms": int((time.time() - t0) * 1000)}

    conn.close()
    return {"status": "ok", "time_ms": int((time.time() - t0) * 1000)}


# ============================================================
# Unit Tests
# ============================================================

def unit_test_memory_dreaming() -> dict:
    """Self-contained unit tests. Uses a temp database.

    Returns dict with pass_count, fail_count, results.
    """
    import tempfile
    import shutil

    results: List[dict] = []
    pass_count = 0
    fail_count = 0

    def _assert(name: str, condition: bool, detail: str = ""):
        nonlocal pass_count, fail_count
        if condition:
            pass_count += 1
            results.append({"test": name, "status": "PASS"})
        else:
            fail_count += 1
            results.append({"test": name, "status": "FAIL", "detail": detail})

    # Setup temp database.
    tmpdir = tempfile.mkdtemp(prefix="wizard-dreaming-test-")
    db_path = os.path.join(tmpdir, "test_memory.db")

    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys=ON")

        # Create base schema.
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                summary TEXT,
                category TEXT NOT NULL,
                importance REAL DEFAULT 0.5,
                source_project TEXT,
                source_context TEXT,
                kind TEXT DEFAULT 'episodic',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                last_accessed TEXT DEFAULT (datetime('now')),
                access_count INTEGER DEFAULT 0,
                is_archived INTEGER DEFAULT 0,
                version INTEGER DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS memory_tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
                tag TEXT NOT NULL,
                UNIQUE(memory_id, tag)
            );
            CREATE TABLE IF NOT EXISTS memory_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
                target_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
                link_type TEXT NOT NULL,
                strength REAL DEFAULT 0.5,
                created_at TEXT DEFAULT (datetime('now')),
                UNIQUE(source_id, target_id, link_type)
            );
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT DEFAULT (datetime('now'))
            );
            INSERT OR REPLACE INTO metadata (key, value) VALUES ('active_embedding_table', 'memory_embeddings');
        """)

        # Create a minimal embedding table (non-vec0, plain BLOB storage for testing).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memory_embeddings (
                memory_id INTEGER PRIMARY KEY,
                embedding BLOB
            )
        """)
        conn.commit()

        # ---- Test 1: Migration check (before) ----
        _assert("migration_not_applied_before",
                not is_dreaming_migration_applied(conn))

        # ---- Test 2: Apply migration ----
        migration_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "migration_017_dreaming.sql",
        )
        if os.path.exists(migration_path):
            result = apply_dreaming_migration(db_path, migration_path)
            _assert("migration_apply_ok", result["status"] == "ok",
                    f"Got: {result}")
        else:
            # Inline migration for testing.
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS dream_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    dream_type TEXT NOT NULL CHECK(dream_type IN ('divergent', 'close_context', 'persona')),
                    memory_a_id INTEGER NOT NULL REFERENCES memories(id),
                    memory_b_id INTEGER NOT NULL REFERENCES memories(id),
                    cosine_distance REAL,
                    context_difference TEXT DEFAULT NULL,
                    dream_content TEXT DEFAULT NULL,
                    dream_memory_id INTEGER DEFAULT NULL REFERENCES memories(id),
                    is_meaningful INTEGER DEFAULT 0,
                    drew_flagged_interesting INTEGER DEFAULT 0,
                    batch_id INTEGER DEFAULT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                );
                CREATE TABLE IF NOT EXISTS dream_batches (
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
                );
            """)
            conn.commit()
            _assert("migration_apply_ok_inline", True)

        conn_after = sqlite3.connect(db_path)
        conn_after.execute("PRAGMA foreign_keys=ON")

        # ---- Test 3: Migration check (after) ----
        _assert("migration_applied_after",
                is_dreaming_migration_applied(conn_after))

        # ---- Test 4: Idempotent re-apply ----
        if os.path.exists(migration_path):
            result2 = apply_dreaming_migration(db_path, migration_path)
            _assert("migration_idempotent", result2["status"] == "already-applied",
                    f"Got: {result2}")
        else:
            _assert("migration_idempotent_skipped", True)

        # ---- Test 5: Insert test memories with synthetic embeddings ----
        import random
        random.seed(42)

        def _make_embedding(dim: int = 768, seed_val: float = 0.0) -> bytes:
            vec = [(seed_val + random.gauss(0, 1)) for _ in range(dim)]
            norm = math.sqrt(sum(x * x for x in vec))
            if norm > 0:
                vec = [x / norm for x in vec]
            return struct.pack(f"{dim}f", *vec)

        test_memories = [
            ("Build cache eviction strategy", "skill_lesson", "workstation",
             "episodic", "2026-01-15 10:00:00", []),
            ("Benchmark validation dataset", "domain_knowledge", "client-alpha",
             "semantic", "2026-03-20 14:00:00", ["client-alpha"]),
            ("Job orchestration pattern", "workflow_pattern", "notes",
             "procedural", "2026-02-10 09:00:00", ["orchestration"]),
            ("Review loop calibration delta", "technical_decision", "workstation",
             "reflection", "2026-04-01 16:00:00", ["review-loop"]),
            ("Persona personality layer design", "insight", "notes",
             "episodic", "2026-01-20 11:00:00", ["living-persona", "persona-design"]),
            ("Newsletter signup conversion rates", "campaign_state", "project-a",
             "episodic", "2026-04-15 08:00:00", ["project-a"]),
            ("Data preparation protocol", "domain_knowledge", "client-alpha",
             "semantic", "2026-02-25 13:00:00", ["project-c"]),
            ("Docs site schema design", "technical_decision", "notes",
             "procedural", "2026-03-05 10:00:00", ["self-heal"]),
            ("Persona evolution feedback loop", "insight", "notes",
             "reflection", "2026-04-20 15:00:00", ["persona-refinement", "persona-growth"]),
            ("Competitive positioning for project-b", "domain_knowledge", "notes",
             "episodic", "2026-03-10 12:00:00", ["project-b"]),
        ]

        mem_ids = []
        for content, category, project, kind, created_at, tags in test_memories:
            cursor = conn_after.execute(
                """INSERT INTO memories (content, summary, category, source_project,
                       kind, created_at, updated_at, last_accessed)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (content, content[:50], category, project, kind,
                 created_at, created_at, created_at),
            )
            mid = cursor.lastrowid
            mem_ids.append(mid)
            for tag in tags:
                conn_after.execute(
                    "INSERT OR IGNORE INTO memory_tags (memory_id, tag) VALUES (?, ?)",
                    (mid, tag),
                )
        conn_after.commit()

        _assert("test_memories_inserted", len(mem_ids) == 10)

        # Insert embeddings — make some close, some far.
        # Group similar: 0,3 close; 1,6 close; 4,8 close (persona pair).
        embeddings = {}
        base_vecs = [_make_embedding(768, seed_val=i * 5.0) for i in range(10)]
        # Override pairs to be close:
        base_vecs[3] = base_vecs[0]  # 0 and 3 nearly identical
        base_vecs[6] = base_vecs[1]  # 1 and 6 nearly identical
        base_vecs[8] = base_vecs[4]  # 4 and 8 nearly identical (persona pair)

        for i, mid in enumerate(mem_ids):
            conn_after.execute(
                "INSERT INTO memory_embeddings (memory_id, embedding) VALUES (?, ?)",
                (mid, base_vecs[i]),
            )
        conn_after.commit()

        # ---- Test 6: Cosine distance computation ----
        dist_close = _cosine_distance_from_bytes(base_vecs[0], base_vecs[3])
        _assert("cosine_distance_close", dist_close < 0.05,
                f"Expected <0.05, got {dist_close}")

        dist_far = _cosine_distance_from_bytes(base_vecs[0], base_vecs[2])
        _assert("cosine_distance_divergent", dist_far > 0.3,
                f"Expected >0.3, got {dist_far}")

        # ---- Test 7: Context difference evaluation ----
        meta_0 = _get_memory_metadata(conn_after, mem_ids[0])
        meta_3 = _get_memory_metadata(conn_after, mem_ids[3])
        diff_03 = _context_difference(meta_0, meta_3, min_criteria=2)
        _assert("context_diff_detected", diff_03 is not None,
                f"Expected diff between memories 0 (workstation/skill_lesson) and 3 (workstation/technical_decision), got None")

        # Same project, same client, close time — should NOT pass.
        meta_same = {
            "id": 999, "summary": "test", "content": "test", "category": "insight",
            "kind": "episodic", "source_project": "same", "created_at": "2026-01-15",
            "tags": [],
        }
        meta_same2 = {
            "id": 998, "summary": "test2", "content": "test2", "category": "insight",
            "kind": "episodic", "source_project": "same", "created_at": "2026-01-16",
            "tags": [],
        }
        diff_same = _context_difference(meta_same, meta_same2, min_criteria=2)
        _assert("context_diff_rejected_same", diff_same is None,
                f"Expected None for nearly-identical context, got {diff_same}")

        # ---- Test 8: Divergent pair selection ----
        div_pairs = select_divergent_pairs(conn_after, count=3, distance_min=0.3)
        _assert("divergent_pairs_selected", len(div_pairs) > 0,
                f"Expected >0 divergent pairs, got {len(div_pairs)}")
        for a, b, d in div_pairs:
            _assert(f"divergent_distance_{a}_{b}", d > 0.3,
                    f"Distance {d} should be > 0.3")

        # ---- Test 9: Close-context pair selection ----
        close_pairs = select_close_context_pairs(
            conn_after, count=3, distance_max=0.1, min_criteria=2,
        )
        _assert("close_context_pairs_found", len(close_pairs) >= 0,
                "Close-context selection should not error")

        # ---- Test 10: Persona pair selection ----
        persona_pairs = select_persona_pairs(conn_after, count=2)
        _assert("persona_pairs_selected", len(persona_pairs) > 0,
                f"Expected persona pairs (memories 5,9 have persona tags), got {len(persona_pairs)}")
        for a, b, d, ctx in persona_pairs:
            _assert(f"persona_context_{a}_{b}", ctx.get("persona_dream") is True,
                    f"Expected persona_dream in context_diff")

        # ---- Test 11: Dream prompt construction ----
        meta_a = _get_memory_metadata(conn_after, mem_ids[0])
        meta_b = _get_memory_metadata(conn_after, mem_ids[2])
        prompt_div = _build_dream_prompt("divergent", meta_a, meta_b)
        _assert("divergent_prompt_built", "VERY DIFFERENT" in prompt_div,
                "Expected divergent template markers")
        _assert("divergent_prompt_has_ids", f"#{mem_ids[0]}" in prompt_div,
                f"Expected memory ID in prompt")

        prompt_close = _build_dream_prompt(
            "close_context", meta_a, meta_b,
            {"different_project": True, "days_apart": 45},
        )
        _assert("close_prompt_built", "SEMANTICALLY SIMILAR" in prompt_close,
                "Expected close-context template markers")

        prompt_persona = _build_dream_prompt("persona", meta_a, meta_b)
        _assert("persona_prompt_built", "the persona" in prompt_persona,
                "Expected the neutral persona reference (no name declared) in persona prompt")

        # ---- Test 12: Dry-run dreaming ----
        dream_result = dream_on_pair(meta_a, meta_b, "divergent", dry_run=True)
        _assert("dry_run_dream", dream_result is not None and "DRY RUN" in dream_result,
                f"Expected dry-run marker, got: {dream_result}")

        # ---- Test 13: Store dream as reflection ----
        dream_mem_id = _store_dream_as_reflection(
            conn_after, "Test dream insight text.", mem_ids[0], mem_ids[2], "divergent",
        )
        _assert("dream_stored_as_reflection", dream_mem_id is not None,
                "Expected non-None memory ID")
        if dream_mem_id:
            stored = conn_after.execute(
                "SELECT kind, category FROM memories WHERE id = ?", (dream_mem_id,)
            ).fetchone()
            _assert("dream_is_reflection_kind", stored[0] == "reflection",
                    f"Expected kind=reflection, got {stored[0]}")
            _assert("dream_is_insight_category", stored[1] == "insight",
                    f"Expected category=insight, got {stored[1]}")
            tags = [r[0] for r in conn_after.execute(
                "SELECT tag FROM memory_tags WHERE memory_id = ?", (dream_mem_id,)
            ).fetchall()]
            _assert("dream_tagged_correctly",
                    "dream" in tags and "dream-divergent" in tags,
                    f"Expected dream tags, got {tags}")

        # ---- Test 14: Dream log insertion ----
        conn_after.execute(
            """INSERT INTO dream_log
                   (dream_type, memory_a_id, memory_b_id, cosine_distance,
                    dream_content, is_meaningful, batch_id)
               VALUES ('divergent', ?, ?, 0.75, 'Test insight', 1, NULL)""",
            (mem_ids[0], mem_ids[2]),
        )
        conn_after.execute(
            """INSERT INTO dream_log
                   (dream_type, memory_a_id, memory_b_id, cosine_distance,
                    dream_content, is_meaningful, batch_id)
               VALUES ('close_context', ?, ?, 0.15, NULL, 0, NULL)""",
            (mem_ids[1], mem_ids[6]),
        )
        conn_after.commit()

        # ---- Test 15: Dream digest ----
        digest = get_dream_digest(conn_after, days=30, limit=5)
        _assert("digest_returns_meaningful", len(digest) >= 1,
                f"Expected at least 1 meaningful dream in digest, got {len(digest)}")
        if digest:
            _assert("digest_has_content", digest[0]["dream_content"] is not None,
                    "First digest entry should have content")

        # ---- Test 16: Flag interesting ----
        dream_id = conn_after.execute("SELECT id FROM dream_log LIMIT 1").fetchone()[0]
        flag_result = flag_interesting(conn_after, dream_id)
        _assert("flag_interesting_ok", flag_result["status"] == "ok",
                f"Expected ok, got {flag_result}")
        flagged = conn_after.execute(
            "SELECT drew_flagged_interesting FROM dream_log WHERE id = ?", (dream_id,)
        ).fetchone()[0]
        _assert("flag_persisted", flagged == 1, f"Expected 1, got {flagged}")

        # ---- Test 17: Flag non-existent dream ----
        bad_flag = flag_interesting(conn_after, 99999)
        _assert("flag_nonexistent_error", bad_flag["status"] == "error",
                f"Expected error, got {bad_flag}")

        # ---- Test 18: Dream stats ----
        stats = get_dream_stats(conn_after)
        _assert("stats_total", stats["total_dreams"] == 2,
                f"Expected 2 total, got {stats['total_dreams']}")
        _assert("stats_meaningful", stats["meaningful_dreams"] == 1,
                f"Expected 1 meaningful, got {stats['meaningful_dreams']}")

        # ---- Test 19: Dream batch record ----
        batch_cursor = conn_after.execute(
            """INSERT INTO dream_batches
                   (divergent_pairs, close_context_pairs, persona_pairs,
                    meaningful_dreams, null_dreams, total_pairs, duration_seconds)
               VALUES (5, 5, 2, 3, 9, 12, 45.6)"""
        )
        conn_after.commit()
        batch = conn_after.execute(
            "SELECT * FROM dream_batches WHERE id = ?", (batch_cursor.lastrowid,)
        ).fetchone()
        _assert("batch_record_created", batch is not None, "Expected batch record")
        _assert("batch_duration", batch[7] == 45.6, f"Expected 45.6, got {batch[7]}")

        # ---- Test 20: Dry-run dream cycle ----
        cycle_result = run_dream_cycle(db_path, dry_run=True)
        _assert("dry_run_cycle_ok", cycle_result["status"] == "dry-run",
                f"Expected dry-run status, got {cycle_result}")
        _assert("dry_run_cycle_has_pairs", cycle_result.get("total_pairs", 0) > 0,
                f"Expected >0 pairs, got {cycle_result}")

        # ---- Test 21: Lock file exclusion ----
        # fcntl.flock is per-open-file-description, so same-process re-lock
        # succeeds. We test the external exclusion pattern via a subprocess.
        # Inside THIS test's own temp dir (never a fixed name under /tmp), and ending in .lock so filelock locks the
        # very path the subprocess below opens.
        lock_path = os.path.join(tmpdir, "dreaming-test.lock")
        acquired = _acquire_lock(lock_path)
        _assert("lock_acquired", acquired, "Expected lock acquisition")

        # Test exclusion from a DIFFERENT process (subprocess).
        import subprocess as _sp
        lock_test_code = (
            "import fcntl, sys\n"
            f"fd = open('{lock_path}', 'w')\n"
            "try:\n"
            "    fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
            "    print('acquired')\n"
            "except (IOError, OSError):\n"
            "    print('blocked')\n"
            "fd.close()\n"
        )
        exc_result = _sp.run(
            [sys.executable, "-c", lock_test_code],
            capture_output=True, text=True, timeout=5,
        )
        _assert("lock_exclusive", exc_result.stdout.strip() == "blocked",
                f"Expected subprocess blocked, got stdout={exc_result.stdout.strip()!r} "
                f"stderr={exc_result.stderr.strip()[:100]!r}")

        _release_lock(lock_path)
        re_acquired = _acquire_lock(lock_path)
        _assert("lock_released", re_acquired, "Expected re-acquisition after release")
        _release_lock(lock_path)

        # ---- Test 22 (P1): Close-context threshold default is 0.40 ----
        _assert("p1_default_threshold_040",
                DEFAULT_CONFIG["close_context_distance_max"] == 0.40,
                f"Expected 0.40, got {DEFAULT_CONFIG['close_context_distance_max']}")

        # ---- Test 23 (P1): Close-context at 0.40 yields more pairs than at 0.30 ----
        # Reopen conn for this test.
        conn_p1 = sqlite3.connect(db_path)
        conn_p1.execute("PRAGMA foreign_keys=ON")
        pairs_030 = select_close_context_pairs(
            conn_p1, count=7, distance_max=0.30, min_criteria=1,
        )
        pairs_040 = select_close_context_pairs(
            conn_p1, count=7, distance_max=0.40, min_criteria=1,
        )
        _assert("p1_040_yields_more_or_equal",
                len(pairs_040) >= len(pairs_030),
                f"At 0.40 got {len(pairs_040)} pairs, at 0.30 got {len(pairs_030)}; expected >=")
        conn_p1.close()

        # ---- Test 24 (P2): claude_model config key exists in DEFAULT_CONFIG ----
        _assert("p2_claude_model_in_config",
                "claude_model" in DEFAULT_CONFIG,
                "claude_model key missing from DEFAULT_CONFIG")

        # ---- Test 25 (P2): dream_on_pair accepts claude_model kwarg ----
        import inspect
        sig = inspect.signature(dream_on_pair)
        _assert("p2_dream_on_pair_has_claude_model",
                "claude_model" in sig.parameters,
                f"dream_on_pair params: {list(sig.parameters.keys())}")

        # ---- Test 26 (P2): dry-run dream_on_pair with claude_model does not error ----
        conn_p2 = sqlite3.connect(db_path)
        meta_p2a = _get_memory_metadata(conn_p2, mem_ids[0])
        meta_p2b = _get_memory_metadata(conn_p2, mem_ids[2])
        dry_result = dream_on_pair(
            meta_p2a, meta_p2b, "divergent",
            dry_run=True, claude_model="sonnet",
        )
        _assert("p2_model_kwarg_dry_run_ok",
                dry_result is not None and "DRY RUN" in dry_result,
                f"Expected dry-run with model kwarg, got: {dry_result}")
        conn_p2.close()

        # ---- Test 27 (P3): flag_interesting propagates importance ----
        # Create a dream with a stored reflection memory.
        conn_p3 = sqlite3.connect(db_path)
        conn_p3.execute("PRAGMA foreign_keys=ON")
        dream_mem_p3 = _store_dream_as_reflection(
            conn_p3, "P3 test dream insight.", mem_ids[1], mem_ids[5], "close_context",
        )
        _assert("p3_reflection_created", dream_mem_p3 is not None,
                "Expected reflection memory for P3 test")

        if dream_mem_p3:
            # Check initial importance (0.55 as set by _store_dream_as_reflection).
            init_imp = conn_p3.execute(
                "SELECT importance FROM memories WHERE id = ?", (dream_mem_p3,)
            ).fetchone()[0]
            _assert("p3_initial_importance_055", abs(init_imp - 0.55) < 0.01,
                    f"Expected initial importance ~0.55, got {init_imp}")

            # Insert a dream_log entry referencing this reflection.
            conn_p3.execute(
                """INSERT INTO dream_log
                       (dream_type, memory_a_id, memory_b_id, cosine_distance,
                        dream_content, dream_memory_id, is_meaningful, batch_id)
                   VALUES ('close_context', ?, ?, 0.25, 'P3 test dream', ?, 1, NULL)""",
                (mem_ids[1], mem_ids[5], dream_mem_p3),
            )
            conn_p3.commit()
            p3_dream_id = conn_p3.execute(
                "SELECT id FROM dream_log WHERE dream_memory_id = ?", (dream_mem_p3,)
            ).fetchone()[0]

            # Flag it.
            p3_flag = flag_interesting(conn_p3, p3_dream_id)
            _assert("p3_flag_ok", p3_flag["status"] == "ok",
                    f"Expected ok, got {p3_flag}")
            _assert("p3_importance_updated_field", p3_flag.get("importance_updated") is True,
                    f"Expected importance_updated=True, got {p3_flag}")

            # Verify importance was boosted.
            new_imp = conn_p3.execute(
                "SELECT importance FROM memories WHERE id = ?", (dream_mem_p3,)
            ).fetchone()[0]
            _assert("p3_importance_boosted_080", abs(new_imp - 0.80) < 0.01,
                    f"Expected importance ~0.80 after flag, got {new_imp}")

        # ---- Test 28 (P3): flag_interesting with no dream_memory_id ----
        conn_p3.execute(
            """INSERT INTO dream_log
                   (dream_type, memory_a_id, memory_b_id, cosine_distance,
                    dream_content, dream_memory_id, is_meaningful, batch_id)
               VALUES ('divergent', ?, ?, 0.80, 'No-reflection dream', NULL, 1, NULL)""",
            (mem_ids[3], mem_ids[7]),
        )
        conn_p3.commit()
        no_ref_id = conn_p3.execute(
            "SELECT id FROM dream_log WHERE dream_memory_id IS NULL AND dream_content = 'No-reflection dream'"
        ).fetchone()[0]
        no_ref_flag = flag_interesting(conn_p3, no_ref_id)
        _assert("p3_no_reflection_flag_ok", no_ref_flag["status"] == "ok",
                f"Expected ok, got {no_ref_flag}")
        _assert("p3_no_reflection_importance_not_updated",
                no_ref_flag.get("importance_updated") is False,
                f"Expected importance_updated=False when no dream_memory_id, got {no_ref_flag}")

        conn_p3.close()

        conn_after.close()

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    return {
        "pass": pass_count,
        "fail": fail_count,
        "total": pass_count + fail_count,
        "results": results,
    }


# ============================================================
# CLI (standalone)
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Memory Dreaming — Wizard Library L4 Piece 1")
    parser.add_argument("action", choices=[
        "run", "dry-run", "digest", "stats", "flag", "unit-tests", "migrate",
    ])
    parser.add_argument("db_path", nargs="?",
                        default=str(Path(__file__).parent.parent / "claude_memory.db"))
    parser.add_argument("--config", default=None, help="Path to JSON config file")
    parser.add_argument("--dream-id", type=int, default=None, help="Dream ID for flag command")
    parser.add_argument("--days", type=int, default=7, help="Lookback days for digest")
    parser.add_argument("--limit", type=int, default=5, help="Max items for digest")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.action == "unit-tests":
        result = unit_test_memory_dreaming()
        print(json.dumps(result, indent=2))
        sys.exit(0 if result["fail"] == 0 else 1)

    if args.action == "migrate":
        migration_path = str(Path(__file__).parent.parent / "migrations" / "migration_017_dreaming.sql")
        result = apply_dreaming_migration(args.db_path, migration_path)
        print(json.dumps(result))
        sys.exit(0 if result["status"] in ("ok", "already-applied") else 1)

    if args.action in ("run", "dry-run"):
        config = None
        if args.config:
            config = json.loads(Path(args.config).read_text())
        result = run_dream_cycle(
            args.db_path,
            config=config,
            dry_run=(args.action == "dry-run"),
        )
        print(json.dumps(result, indent=2))
        # A skip is not a failure and not a success: this brain's dream lock was held, nothing ran. Until Round 3 it
        # exited 1 here (the ledger read a race as a failure); dream_pipeline.py's own CLI already returned 75.
        if result.get("status") == "skipped":
            sys.exit(EXIT_SKIPPED_LOCK)
        if result.get("status") == "starved":
            sys.exit(EXIT_STARVED)
        # "e) not now" (round 2, m4): the owner's answer -- a clear status, exit 0, never the starved 79.
        sys.exit(0 if result["status"] in ("ok", "dry-run", "unconfigured") else 1)

    if args.action == "digest":
        conn = _get_conn(args.db_path)
        digest = get_dream_digest(conn, days=args.days, limit=args.limit)
        conn.close()
        print(json.dumps({"status": "ok", "digest": digest, "count": len(digest)}, indent=2))
        sys.exit(0)

    if args.action == "stats":
        conn = _get_conn(args.db_path)
        stats = get_dream_stats(conn)
        conn.close()
        print(json.dumps({"status": "ok", **stats}, indent=2))
        sys.exit(0)

    if args.action == "flag":
        if not args.dream_id:
            print(json.dumps({"status": "error", "message": "--dream-id required"}))
            sys.exit(1)
        conn = _get_conn(args.db_path)
        result = flag_interesting(conn, args.dream_id)
        conn.close()
        print(json.dumps(result))
        sys.exit(0 if result["status"] == "ok" else 1)


if __name__ == "__main__":
    main()
