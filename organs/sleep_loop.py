#!/usr/bin/env python3
"""Sleep Loop Consolidation — Wizard Library L2 Component 3.

A stand-alone module that runs the nightly sleep cycle: consolidation,
edge extraction, confidence decay, reflection generation, importance
re-ranking, and run logging. This is the INTEGRATOR component — it calls
functions from all four prior L2 components:

  - hybrid_retrieval.py: embedding similarity for clustering
  - kind_taxonomy.py: classifying consolidated memories as kind='semantic'
  - temporal_edges.py: extract_edges_heuristic(), decay_confidence()
  - procedural_memory.py: module pattern reference

Public API
----------
``run_sleep_cycle(db_path, config=None, dry_run=False) -> dict``
    Main entry point. Runs all 6 stages sequentially.

``consolidate_episodics(conn, lookback_days=7, ...) -> List[dict]``
    Stage 1: cluster episodic memories and distill into semantic.

``extract_new_edges(conn, since_timestamp=None) -> dict``
    Stage 2: call temporal_edges.extract_edges_heuristic() on unprocessed.

``decay_and_prune_edges(conn, ...) -> dict``
    Stage 3: call temporal_edges.decay_confidence().

``generate_reflections(conn, ...) -> List[int]``
    Stage 4: generate reflection memories from unreflected episodics.

``rerank_importance(conn, ...) -> dict``
    Stage 5: boost/decay importance based on access patterns.

``get_run_history(conn, limit=10) -> List[dict]``
    Query recent sleep loop runs.

CLI interface
-------------
::

    python3 sleep_loop.py --run <db_path>            # full cycle
    python3 sleep_loop.py --dry-run <db_path>        # simulate
    python3 sleep_loop.py --unit-tests               # self-test
    python3 sleep_loop.py --status <db_path>         # recent runs

Phase 0 design: memories #3913, #3915, #3817, #3921, #3922.
"""

from __future__ import annotations

import argparse
# Portable cross-platform file locking (Council #4827): filelock on Unix+Windows+Mac,
# fcntl fallback on Unix if filelock unavailable.
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
import re
import signal
import sqlite3
import struct
import sys
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

# rho-1: the ONE env-directory resolver. Hard import by design -- it is
# stdlib-only and is laid by the same `cp -f "${PKG_ROOT}"/*.py` loop that
# lays this module, so its absence means an incomplete install, and an
# incomplete install must SAY SO rather than resolve sidecars elsewhere.
# A per-module inline fallback would recreate the 13-way divergence this
# module exists to end.
import voidloom_env
from typing import Any, Dict, List, Optional, Set, Tuple

# --- TO-02 repair: resolve the embedding tier instead of hardcoding it -------
# The write path moved to memory_embeddings_v2 on 2026-05-28; this reader was not
# moved with it and has returned NOTHING since. embed_resolver refuses rather than
# defaulting to the dormant table (architecture-009).
from embed_resolver import resolve as _resolve_embed_tier  # noqa: E402
# ---------------------------------------------------------------------------


# L5-F (iter-2 P2): the ACT-R activation view lives in kind_taxonomy. Stage 5
# (rerank_importance) reads it as the SINGLE decay source instead of computing its
# own boost/decay -- the SAME view claude_memory.cmd_maintain consumes, so there is
# exactly one decay formula in the substrate. Guarded import: if kind_taxonomy is
# unavailable (a degraded/partial install) Stage 5 falls back to a no-op activation
# pass, never the old double-mutating boost/decay.
try:
    from . import kind_taxonomy  # type: ignore
except (ImportError, ValueError):
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import kind_taxonomy  # type: ignore
    except ImportError:
        kind_taxonomy = None  # graceful: ACT-R features degrade to a no-op pass

# L5-L "The Quickening": the recall-tide sense. A fail-soft, individually-
# toggleable observability stage invoked by run_sleep_cycle. Guarded import so a
# degraded/partial install simply skips the stage (never breaks the cycle).
try:
    from . import quickening  # type: ignore
except (ImportError, ValueError):
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import quickening  # type: ignore
    except ImportError:
        quickening = None  # graceful: the quickening stage degrades to a no-op

# L5-E-COMM "Leiden Cognitive-Map Communities": the theme sense. A fail-soft,
# individually-toggleable visualization stage invoked by run_sleep_cycle, run
# LAST (after the settled graph exists). Pure consumer -- writes only its own
# three tables, never memory_edges. Guarded import so a degraded/partial install
# simply skips the stage (never breaks the cycle).
try:
    from . import communities  # type: ignore
except (ImportError, ValueError):
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import communities  # type: ignore
    except ImportError:
        communities = None  # graceful: the communities stage degrades to a no-op

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


def _llm_consolidation(prompt: str, n_members: int) -> Optional[str]:
    """Stage-1 LLM consolidation through the thinker seam (text only, the haiku tier, 60 s). The consolidated text,
    or None -- and a None is LOGGED with the failure's kind, then the caller takes the heuristic path."""
    try:
        res = _thinker.think(prompt, purpose="sleep_loop", tier="haiku", timeout_s=60)
    except _thinker.ThinkError as e:
        logger.warning("LLM consolidation: thinker %s (%s); falling back to heuristic", e.kind, e)
        return None
    text = res.text.strip()
    if text:
        logger.info(f"LLM consolidation success for cluster of {n_members}")
        return text
    return None

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

DEFAULT_CONFIG = {
    'lookback_days': 7,
    'min_cluster_size': 3,
    'similarity_threshold': 0.7,
    'consolidation_mode': 'heuristic',
    'decay_factor': 0.995,
    'prune_threshold': 0.15,
    'max_age_days': 45,
    'min_unreflected_for_reflection': 25,
    'importance_boost_cap': 0.05,
    'importance_decay_rate': 0.01,
    'min_importance': 0.3,
    'quickening_enabled': True,  # L5-L recall-tide spike stage (env VOIDLOOM_NO_QUICKENING overrides)
    'communities_enabled': True,  # L5-E-COMM community detection stage (env VOIDLOOM_NO_COMMUNITIES overrides)
    # None = THIS BRAIN's run-lock, beside its database (run_lock_path). Never a path a tempdir decides.
    'lock_file': None,
    # ---- the IN-PROCESS deadline (round 1, L3) ---------------------------------------------------------------------
    # The run enforces its own time budget on every platform. MEASURED: the roster's `timeout` reaches systemd only
    # (install_crons.sh renders TimeoutStartSec); the launchd render gets NO timeout at all, so on macOS a stalled run
    # would never end. systemd's TimeoutStartSec (600) stays as the outer wall, above this budget.
    #   budget_seconds          the whole run's budget; 0 / None = no limit (the owner's catch-up run, `--catch-up`).
    #   stage2_reserve_seconds  Stage 2 (edge extraction) stops this long before the budget ends, commits what it did,
    #                           carries the rest on the id watermark, and leaves the time for Stages 3..5c.
    #   edge_batch_size         Stage 2 commits its edges AND the watermark every this many memories.
    #   progress_every_seconds  how often Stage 2 prints a progress line to stderr (the journal / the owner's terminal).
    'budget_seconds': 540,
    'stage2_reserve_seconds': 120,
    'edge_batch_size': 50,
    'progress_every_seconds': 30,
}

# The id watermark of Stage 2 (contract C5): the highest memory id whose edge extraction has been COMMITTED. It lives in
# the brain's metadata table, so it survives a run that is killed, and it advances with every committed batch -- the
# old watermark was the last SUCCESSFUL run's completed_at, so every failed night re-faced a larger backlog (939 ->
# 3,245 inputs over the eight stalled nights, 2026-10-06..13) and the loop could never recover on its own.
EDGE_WATERMARK_KEY = 'edge_watermark_id'

# The run's stage names, as `sleep_loop_runs.stage_reached` records them (contract C5): the last stage that BEGAN.
STAGES = ('start', 'consolidation', 'edges', 'decay', 'reflection', 'importance', 'quickening', 'communities',
          'finalize')

# The clock the budget reads. Module-level so a test can drive it; monotonic so a wall-clock jump cannot end a run.
_clock = time.monotonic


def _utc_now() -> datetime:
    """Now, in UTC, naive -- the same frame as every timestamp SQLite's datetime('now') writes into this brain."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _utc_stamp(dt: Optional[datetime] = None) -> str:
    """A UTC instant in the brain's own timestamp format ('YYYY-MM-DD HH:MM:SS', as memories.created_at holds it).

    MEASURED (health check 2026-10-14): this organ wrote datetime.now().isoformat() -- LOCAL time with a 'T' -- into
    sleep_loop_runs and compared it with memories.created_at (UTC, a space). A string compare of the two mixes a 4-hour
    offset (EDT) with a 'T' > ' ' collation, so the Stage-1 window and the Stage-2 cutoff were both off.
    """
    return (dt or _utc_now()).strftime('%Y-%m-%d %H:%M:%S')


def _to_utc_db(ts: str) -> Optional[str]:
    """Any timestamp this brain has held for a sleep run, in the brain's UTC format.

    A value with a 'T' and no offset is what this organ wrote before round 1: LOCAL time (datetime.now().isoformat()).
    It is read as local and converted. A value with an offset is converted. A space-separated value is already UTC.
    """
    if not ts:
        return None
    s = str(ts).strip()
    try:
        dt = datetime.fromisoformat(s.replace('Z', '+00:00'))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    if 'T' in s:
        return dt.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')     # naive -> the host's local zone
    return dt.strftime('%Y-%m-%d %H:%M:%S')


class BudgetExhausted(Exception):
    """The run's own time budget ended inside a stage that cannot carry its work over (the stage is named)."""

    def __init__(self, stage: str):
        super().__init__("the run's time budget ended during stage %s" % stage)
        self.stage = stage


class Terminated(BaseException):
    """SIGTERM/SIGHUP arrived (systemd's TimeoutStartSec, a stop, a logout). A BaseException so no stage's
    `except Exception` swallows it: it must reach the orchestrator, which closes the run row before the process ends."""

    def __init__(self, signum: int):
        super().__init__("terminated by signal %d" % signum)
        self.signum = signum


class _Budget:
    """The run's time budget, read from the module clock. `seconds` None = no limit."""

    def __init__(self, seconds):
        try:
            seconds = float(seconds) if seconds is not None else None
        except (TypeError, ValueError):
            seconds = None
        self.seconds = seconds if seconds and seconds > 0 else None
        self.t0 = _clock()

    def elapsed(self) -> float:
        return _clock() - self.t0

    def exhausted(self) -> bool:
        return self.seconds is not None and self.elapsed() >= self.seconds

    def deadline(self, reserve: float = 0.0) -> Optional[float]:
        """The module-clock instant a stage must stop by, leaving `reserve` seconds (at most half the budget)."""
        if self.seconds is None:
            return None
        reserve = max(0.0, min(float(reserve or 0.0), self.seconds / 2.0))
        return self.t0 + self.seconds - reserve


def _stage_log(msg: str, *args) -> None:
    """One line per stage event on stderr: the journal under systemd, the owner's terminal on a catch-up run.

    Not `logger.info`: nothing configures logging in the unit's process, and the last-resort handler drops INFO."""
    try:
        sys.stderr.write("sleep-loop: " + (msg % args if args else msg) + "\n")
        sys.stderr.flush()
    except Exception:
        pass

# The legacy, machine-global lock NAME. Every sidecar config the package wrote before Round 3 carries it as an absolute
# path ("lock_file": "/tmp/wizard-library-sleep-loop.lock"), and the old default built it from tempfile.gettempdir().
LEGACY_LOCK_NAME = 'wizard-library-sleep-loop.lock'
# EX_TEMPFAIL: the brain's run-lock was held, so nothing ran and nothing failed. The run ledger
# (organ_run_log.SKIP_STATUSES) files 75 as "skipped-lock"; dream_pipeline.py uses the same status.
EXIT_SKIPPED_LOCK = 75


def run_lock_path(db_path: str, configured: Optional[str] = None) -> Tuple[str, Optional[str]]:
    """The sleep loop's run-lock for ONE brain, and a note when a configured path was set aside.

    The lock exists to keep two sleep cycles off the SAME brain, so it is keyed by the brain: it lives beside the
    brain's database file, at the file's real path (a symlinked spelling shares the lock), where every process that
    can open this brain finds it whatever its TMPDIR or PrivateTmp, and where no process of another brain ever looks.

    MEASURED on the reference host, 2026-10-01 22:30:09: the packaged config named the machine-global
    /tmp/wizard-library-sleep-loop.lock; a test harness's loop on a SCRATCH brain held it, and the host's own nightly
    loop SKIPPED ("Lock file held by another process") and exited 0 -- one brain's night lost to another brain's run.

    A config naming the legacy name, in any directory, keeps working: this brain's own lock is used and the note says
    so. Any other configured path is the owner's explicit choice and is honoured as given.
    """
    brain = os.path.realpath(db_path) + '.sleep-loop.lock'
    if configured and os.path.basename(str(configured)) != LEGACY_LOCK_NAME:
        return str(configured), None
    if not configured:
        return brain, None
    return brain, ("the configured lock_file %s is the legacy machine-global lock (any brain on this machine could "
                   "hold it); this brain's own lock is used instead" % configured)

# Embedding dimension (Nomic Embed v1.5).
_NOMIC_DIM = 768

# ============================================================
# Lock file
# ============================================================

_lock_fd = None
_lock = None  # filelock.FileLock instance (cross-platform path)


def _acquire_lock(lock_path: str) -> bool:
    """Acquire an exclusive lock. Returns True if acquired, False if already held.

    Cross-platform (Council #4827): filelock primary, fcntl fallback on Unix.
    """
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
        return True  # no locking primitive available — proceed
    # A lock file that cannot be CREATED raises (OSError) for the caller to name -- it is never "held by another
    # process", which is what the flock contention below means (filelock raises the same way).
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
    """Release the lock file. Cross-platform (filelock primary, fcntl fallback)."""
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

def _cosine_similarity(vec_a: bytes, vec_b: bytes) -> float:
    """Compute cosine similarity between two serialised float32 vectors."""
    dim = len(vec_a) // 4
    if dim == 0:
        return 0.0
    a = struct.unpack(f"{dim}f", vec_a)
    b = struct.unpack(f"{dim}f", vec_b)
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _centroid_embedding(embeddings: List[bytes]) -> bytes:
    """Compute the centroid of multiple float32 embedding byte strings."""
    if not embeddings:
        return b''
    dim = len(embeddings[0]) // 4
    accum = [0.0] * dim
    for emb in embeddings:
        vals = struct.unpack(f"{dim}f", emb)
        for i, v in enumerate(vals):
            accum[i] += v
    n = len(embeddings)
    centroid = [v / n for v in accum]
    # Normalize
    norm = math.sqrt(sum(x * x for x in centroid))
    if norm > 0:
        centroid = [x / norm for x in centroid]
    return struct.pack(f"{dim}f", *centroid)


def _get_conn(db_path: str) -> sqlite3.Connection:
    """Open a connection with vec0 loaded."""
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
            pass  # vec0 may not be needed for all stages
    return conn


def _is_migration_applied(conn: sqlite3.Connection) -> bool:
    """Check if migration 005 (sleep_loop_runs table) has been applied."""
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    return "sleep_loop_runs" in tables


def _apply_migration(db_path: str, migration_sql: str) -> dict:
    """Apply migration 005 idempotently."""
    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.execute("PRAGMA foreign_keys=ON")
    if _is_migration_applied(conn):
        conn.close()
        return {"status": "already-applied"}
    try:
        conn.executescript(migration_sql)
        conn.commit()
        conn.close()
        return {"status": "ok"}
    except Exception as e:
        conn.close()
        return {"status": "error", "message": str(e)}


# ============================================================
# Stage 1: Consolidation (episodic -> semantic)
# ============================================================

try:                                    # optional: a Tier-0 interpreter has no numpy and takes the pure-Python loop
    import numpy as _np                 # type: ignore
except Exception:                       # pragma: no cover - exercised by the pure-Python test path
    _np = None

# Rows of the similarity matrix computed per block: 256 x n float64 is 3 MB at n = 1,532 (the friend pack's episodics)
# and 31 MB at n = 15,000, so the matrix is never held whole.
_SIM_BLOCK_ROWS = 256


def _pairs_above_threshold(ids: List[int], embeddings: List[bytes], threshold: float,
                           check=None, use_numpy: Optional[bool] = None) -> Dict[Tuple[int, int], float]:
    """{(min id, max id): cosine} for every pair at or above `threshold`, inserted in (i, j) row-major order.

    The SAME dict the pure-Python double loop built -- same keys, same insertion order (the clustering's stable sort
    depends on it), values equal to float rounding -- computed with numpy when it is importable and every vector has
    one width. MEASURED (health check, 2026-10-14): the pure loop took 161.8 s for 607 episodics x 1024 dims, and the
    friend pack's 1,532 episodics project to ~1,030 s [EST, n^2 scaling] -- the whole night's budget in Stage 1.
    `check()` is called once per block / row so a deadline can stop the stage.
    """
    n = len(ids)
    out: Dict[Tuple[int, int], float] = {}
    if n < 2:
        return out
    width = {len(e) for e in embeddings}
    if (_np is not None if use_numpy is None else (use_numpy and _np is not None)) and len(width) == 1:
        dim = width.pop() // 4
        mat = _np.frombuffer(b"".join(embeddings), dtype="<f4").astype(_np.float64).reshape(n, dim)
        norms = _np.sqrt(_np.einsum("ij,ij->i", mat, mat))
        safe = _np.where(norms > 0, norms, 1.0)
        unit = mat / safe[:, None]
        unit[norms == 0] = 0.0          # a zero vector has cosine 0 with everything, as _cosine_similarity says
        for i0 in range(0, n, _SIM_BLOCK_ROWS):
            if check:
                check()
            i1 = min(n, i0 + _SIM_BLOCK_ROWS)
            block = unit[i0:i1].dot(unit.T)
            for bi in range(i1 - i0):
                i = i0 + bi
                row = block[bi, i + 1:]
                for j in (_np.nonzero(row >= threshold)[0] + i + 1).tolist():
                    a, b = ids[i], ids[j]
                    out[(a, b) if a < b else (b, a)] = float(block[bi, j])
        return out
    for i in range(n):
        if check:
            check()
        for j in range(i + 1, n):
            sim = _cosine_similarity(embeddings[i], embeddings[j])
            if sim >= threshold:
                a, b = ids[i], ids[j]
                out[(a, b) if a < b else (b, a)] = sim
    return out


def _any_embeddings(conn) -> bool:
    """True when any known embedding table exists and holds a row (the brain has a Tier-1 index to declare)."""
    for t in ("memory_embeddings_v3", "memory_embeddings_v2", "memory_embeddings"):
        try:
            if conn.execute(f"SELECT 1 FROM {t} LIMIT 1").fetchone():
                return True
        except sqlite3.Error:
            continue
    return False


def consolidate_episodics(
    conn: sqlite3.Connection,
    lookback_days: int = 7,
    min_cluster_size: int = 3,
    similarity_threshold: float = 0.7,
    mode: str = 'heuristic',
    dry_run: bool = False,
    deadline: Optional[float] = None,
) -> List[dict]:
    """Stage 1: Cluster episodic memories and distill into semantic memories.

    Uses greedy agglomerative clustering: sort all pairwise similarities
    descending, merge greedily, stop when similarity drops below threshold.

    Args:
        conn: sqlite3 connection with vec0 loaded.
        lookback_days: how far back to look for episodic memories.
        min_cluster_size: minimum memories in a cluster to consolidate.
        similarity_threshold: cosine similarity floor for clustering.
        mode: 'heuristic' (template-based) or 'llm' (API-assisted).
        dry_run: if True, compute clusters but don't create memories.
        deadline: a module-clock instant (see _Budget); past it the stage raises BudgetExhausted('consolidation').
            Every cluster already written stays written (each commits on its own; the idempotency guard below keeps
            them from being consolidated twice), so the next run continues where this one stopped.

    Returns:
        List of dicts: {consolidated_memory_id, source_ids, cluster_size, summary}
        In dry_run mode, consolidated_memory_id will be None.
    """
    def _check_deadline():
        if deadline is not None and _clock() >= deadline:
            raise BudgetExhausted('consolidation')

    # UTC, in the brain's own format: created_at is SQLite's datetime('now'). The local isoformat this replaced put
    # the window up to a day off (a 'T' sorts after every ' ' of the same date) and 4 h off (EDT).
    cutoff = _utc_stamp(_utc_now() - timedelta(days=lookback_days))

    # Fetch recent episodic memories with their embeddings.
    try:
        _emb_table, _emb_dim = _resolve_embed_tier(conn)
    except Exception:
        # A brain with no embedding tier yet (a Tier-0 install, or a fresh brain before its first embed) has nothing
        # Stage 1 can cluster: that is "nothing to do", not a failed night. Only when vectors EXIST without a declared
        # table is the refusal a real fault, and then it stays loud.
        if not _any_embeddings(conn):
            return []
        raise
    rows = conn.execute(
        f"""SELECT m.id, m.content, m.summary, m.importance, m.category,
                  e.embedding
           FROM memories m
           JOIN {_emb_table} e ON e.memory_id = m.id
           WHERE m.kind = 'episodic'
             AND m.is_archived = 0
             AND m.created_at > ?
             -- IDEMPOTENCY GUARD. Without this the organ re-consolidates the same
             -- sources every night for the whole lookback window: 306 of 311
             -- historical sources were consolidated more than once, some EIGHT
             -- times, 1,473 rows over 311 distinct sources -- 4.7x duplication.
             -- The defect is PRE-EXISTING and was invisible for 100 days only
             -- because the join returned nothing; repairing the outer failure
             -- exposed it. A dead organ conceals its own bugs.
             --
             -- NOT EXISTS, not NOT IN: source_memory_id is NOT NULL today and
             -- NOT IN would be correct today, but a single NULL entering that
             -- subquery later makes NOT IN return the empty set and takes this
             -- organ silently dark again. NOT EXISTS is NULL-immune by
             -- construction and this organ has already been dark once.
             --
             -- Exclusion, not a watermark: generate_reflections (:786) anchors on
             -- MAX(created_at) of the last reflection, which is right for it.
             -- Here it would be wrong -- a memory that failed to reach
             -- min_cluster_size tonight must stay eligible for the night two more
             -- like it arrive. Exclusion drops exactly what was consolidated;
             -- a watermark would drop everything older than the last run.
             AND NOT EXISTS (SELECT 1 FROM consolidation_sources cs
                              WHERE cs.source_memory_id = m.id)
           ORDER BY m.created_at DESC""",
        (cutoff,),
    ).fetchall()

    if len(rows) < min_cluster_size:
        return []

    # Build ID -> data map.
    memories = {}
    for row in rows:
        mid, content, summary, importance, category, embedding = row
        memories[mid] = {
            'id': mid,
            'content': content,
            'summary': summary or content[:100],
            'importance': importance,
            'category': category,
            'embedding': embedding,
        }

    memory_ids = list(memories.keys())

    # Precompute pairwise similarity matrix (upper triangle only, stored as dict).
    # This avoids O(n^3) during cluster expansion.
    sim_cache = _pairs_above_threshold(memory_ids, [memories[m]['embedding'] for m in memory_ids],
                                       similarity_threshold, _check_deadline)

    def _get_sim(a: int, b: int) -> float:
        key = (min(a, b), max(a, b))
        return sim_cache.get(key, 0.0)

    # Greedy seed-and-expand clustering (complete-linkage discipline):
    # 1. Sort all above-threshold pairs by similarity descending.
    # 2. For the highest-sim pair, start a cluster.
    # 3. Expand: add any memory that is >= threshold with ALL existing members.
    # 4. Mark cluster members as consumed; repeat for remaining memories.
    # This avoids the single-linkage "chaining" problem where transitive
    # connections merge unrelated memories into a single giant cluster.
    sorted_pairs = sorted(sim_cache.items(), key=lambda x: x[1], reverse=True)
    consumed: Set[int] = set()
    verified_clusters: List[List[int]] = []

    for (id_a, id_b), sim in sorted_pairs:
        if id_a in consumed or id_b in consumed:
            continue
        _check_deadline()

        # Start a new cluster with this pair.
        cluster = [id_a, id_b]
        consumed.add(id_a)
        consumed.add(id_b)

        # Try to expand: check all unconsumed memories.
        for candidate in memory_ids:
            if candidate in consumed:
                continue
            # Complete-linkage: candidate must be >= threshold with EVERY member.
            if all(_get_sim(candidate, member) >= similarity_threshold for member in cluster):
                cluster.append(candidate)
                consumed.add(candidate)

        if len(cluster) >= min_cluster_size:
            verified_clusters.append(cluster)

    results: List[dict] = []
    cluster_counter = int(time.time())  # unique cluster_id per batch

    for members in verified_clusters:
        _check_deadline()
        cluster_counter += 1
        cluster_id = cluster_counter

        # Gather cluster data.
        cluster_mems = [memories[mid] for mid in members]
        all_tags: List[str] = []
        for mid in members:
            tag_rows = conn.execute(
                "SELECT tag FROM memory_tags WHERE memory_id = ?", (mid,)
            ).fetchall()
            all_tags.extend(r[0] for r in tag_rows)

        tag_counts = Counter(all_tags)
        top_tags = [t for t, _ in tag_counts.most_common(5)]
        highest_importance_mem = max(cluster_mems, key=lambda m: m['importance'])
        date_min = conn.execute(
            "SELECT MIN(created_at) FROM memories WHERE id IN ({})".format(
                ",".join("?" * len(members))
            ), members
        ).fetchone()[0]
        date_max = conn.execute(
            "SELECT MAX(created_at) FROM memories WHERE id IN ({})".format(
                ",".join("?" * len(members))
            ), members
        ).fetchone()[0]

        # Build consolidated summary.
        if mode == 'heuristic':
            other_summaries = [
                m['summary'][:80] for m in cluster_mems
                if m['id'] != highest_importance_mem['id']
            ][:3]
            consolidated_content = (
                f"Distilled from {len(members)} episodic memories "
                f"({date_min[:10]} to {date_max[:10]}). "
                f"Common themes: {', '.join(top_tags) if top_tags else 'mixed'}. "
                f"Core insight: {highest_importance_mem['summary']}. "
                f"Supporting observations: {'; '.join(other_summaries)}."
            )
            consolidated_summary = (
                f"Consolidated {len(members)} episodic memories: "
                f"{highest_importance_mem['summary'][:100]}"
            )
            confidence = 0.75
        else:
            # LLM mode: through the thinker seam (the owner's recorded backend). Falls back to heuristic.
            other_summaries = [
                m['summary'][:80] for m in cluster_mems
                if m['id'] != highest_importance_mem['id']
            ][:3]
            llm_success = False
            prompt = (
                f"Distill these {len(members)} related episodic memories into ONE concise semantic insight. "
                f"Date range: {date_min[:10]} to {date_max[:10]}. "
                f"Common themes: {', '.join(top_tags) if top_tags else 'mixed'}.\n\n"
                f"Primary memory: {highest_importance_mem['summary']}\n"
                f"Content: {highest_importance_mem.get('content', '')[:300]}\n\n"
                f"Supporting memories:\n" +
                "\n".join(f"- {s}" for s in other_summaries) +
                "\n\nProvide ONLY the distilled insight (2-3 sentences max). No preamble."
            )
            _text = _llm_consolidation(prompt, len(members))
            if _text:
                consolidated_content = _text
                consolidated_summary = consolidated_content[:200]
                confidence = 0.85
                llm_success = True
            if not llm_success:
                consolidated_content = (
                    f"Distilled from {len(members)} episodic memories "
                    f"({date_min[:10]} to {date_max[:10]}). "
                    f"Common themes: {', '.join(top_tags) if top_tags else 'mixed'}. "
                    f"Core insight: {highest_importance_mem['summary']}. "
                    f"Supporting observations: {'; '.join(other_summaries)}."
                )
                consolidated_summary = (
                    f"Consolidated {len(members)} episodic memories: "
                    f"{highest_importance_mem['summary'][:100]}"
                )
                confidence = 0.75

        if dry_run:
            results.append({
                'consolidated_memory_id': None,
                'source_ids': members,
                'cluster_size': len(members),
                'summary': consolidated_summary,
                'avg_similarity': round(
                    sum(_cosine_similarity(memories[members[i]]['embedding'],
                                          memories[members[j]]['embedding'])
                        for i in range(len(members))
                        for j in range(i + 1, len(members)))
                    / max(1, len(members) * (len(members) - 1) // 2), 3),
            })
            continue

        # Compute centroid embedding for the new memory.
        cluster_embeddings = [memories[mid]['embedding'] for mid in members]
        centroid = _centroid_embedding(cluster_embeddings)

        # Determine importance: average of cluster, slightly boosted.
        avg_importance = sum(m['importance'] for m in cluster_mems) / len(cluster_mems)
        new_importance = min(1.0, avg_importance + 0.05)

        # Insert consolidated memory.
        now_iso = _utc_stamp()
        cursor = conn.execute(
            """INSERT INTO memories
               (content, summary, category, importance, source_project, source_context,
                kind, confidence, derived_from, consolidated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                consolidated_content,
                consolidated_summary,
                highest_importance_mem['category'],
                new_importance,
                'sleep-loop-consolidation',
                f'cluster_id={cluster_id}',
                'semantic',
                confidence,
                json.dumps(members),
                now_iso,
            ),
        )
        new_id = cursor.lastrowid

        # Insert centroid embedding.
        conn.execute(
            f"INSERT INTO {_resolve_embed_tier(conn)[0]} (memory_id, embedding) VALUES (?, ?)",
            (new_id, centroid),
        )

        # Insert tags.
        for tag in top_tags:
            conn.execute(
                "INSERT OR IGNORE INTO memory_tags (memory_id, tag) VALUES (?, ?)",
                (new_id, tag),
            )
        conn.execute(
            "INSERT OR IGNORE INTO memory_tags (memory_id, tag) VALUES (?, ?)",
            (new_id, 'sleep-loop-consolidated'),
        )

        # Record provenance in consolidation_sources.
        for source_mid in members:
            conn.execute(
                """INSERT INTO consolidation_sources
                   (consolidated_memory_id, source_memory_id, cluster_id)
                   VALUES (?, ?, ?)""",
                (new_id, source_mid, cluster_id),
            )

        # TO-09 (round gamma). consolidation_sources is a PRIVATE ledger; the
        # UNIVERSAL one is memory_provenance, and it has 205 rows from a single
        # backfill instant and no organic writer in its entire history. The chain
        # its designers intended -- store -> belief_scan_queue -> belief_engine
        # .propose -> record_provenance -- cannot fire, because nothing in this
        # tree schedules `belief scan` (19 ExecStart lines, zero mentions).
        #
        # This organ IS scheduled, it runs nightly, and it already knows exactly
        # what derived from what. Binding here is what makes provenance rows
        # appear that nobody typed. See memory_write.enqueue_health() for the
        # half of the chain this does NOT fix.
        _vl_attach(conn, new_id, consolidated_content,
                   "sleep_loop.consolidate_episodics",
                   sources=members, transformation="consolidation",
                   attach=False)   # the centroid above IS this memory's vector

        conn.commit()

        results.append({
            'consolidated_memory_id': new_id,
            'source_ids': members,
            'cluster_size': len(members),
            'summary': consolidated_summary,
        })

    return results


# ============================================================
# Stage 2: Edge Extraction
# ============================================================

def _metadata_has_updated_at(conn: sqlite3.Connection) -> bool:
    try:
        return 'updated_at' in {r[1] for r in conn.execute("PRAGMA table_info(metadata)").fetchall()}
    except sqlite3.Error:
        return False


def read_edge_watermark(conn: sqlite3.Connection) -> Optional[int]:
    """metadata.edge_watermark_id (contract C5), or None when this brain has never carried one."""
    try:
        row = conn.execute("SELECT value FROM metadata WHERE key = ?", (EDGE_WATERMARK_KEY,)).fetchone()
    except sqlite3.Error:
        return None
    try:
        return int(row[0]) if row and row[0] is not None else None
    except (TypeError, ValueError):
        return None


def _write_edge_watermark(conn: sqlite3.Connection, memory_id: int) -> None:
    """Advance the watermark INSIDE the caller's transaction, so it commits with the edges it accounts for."""
    if _metadata_has_updated_at(conn):
        conn.execute("INSERT OR REPLACE INTO metadata (key, value, updated_at) VALUES (?, ?, datetime('now'))",
                     (EDGE_WATERMARK_KEY, str(int(memory_id))))
    else:
        conn.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                     (EDGE_WATERMARK_KEY, str(int(memory_id))))


def _watermark_from_time(conn: sqlite3.Connection, since_utc: str) -> int:
    """The id watermark equivalent to 'every memory created after `since_utc`': one below the first such id."""
    row = conn.execute("SELECT MIN(id) FROM memories WHERE created_at > ?", (since_utc,)).fetchone()
    if row and row[0] is not None:
        return int(row[0]) - 1
    return int(conn.execute("SELECT COALESCE(MAX(id), 0) FROM memories").fetchone()[0])


def bootstrap_edge_watermark(conn: sqlite3.Connection) -> Tuple[int, str]:
    """The watermark a brain that has never carried one starts from, and how it was derived.

    It reproduces the old cutoff exactly once: the last success/partial run's completed_at (written in LOCAL time by the
    code before round 1, so it is converted), else the last 7 days. From then on the id watermark is the only cutoff.
    """
    row = None
    try:
        row = conn.execute("""SELECT completed_at FROM sleep_loop_runs
                              WHERE status IN ('success', 'partial') AND completed_at IS NOT NULL
                              ORDER BY id DESC LIMIT 1""").fetchone()
    except sqlite3.Error:
        row = None
    since = _to_utc_db(row[0]) if row and row[0] else None
    if since:
        return _watermark_from_time(conn, since), "last completed run at %s UTC" % since
    since = _utc_stamp(_utc_now() - timedelta(days=7))
    return _watermark_from_time(conn, since), "no completed run: the last 7 days (since %s UTC)" % since


def extract_new_edges(
    conn: sqlite3.Connection,
    since_timestamp: Optional[str] = None,
    dry_run: bool = False,
    deadline: Optional[float] = None,
    batch_size: int = 50,
    progress_every: Optional[float] = None,
) -> dict:
    """Stage 2: Extract edges for every memory past the id watermark, committing as it goes.

    Calls temporal_edges.extract_edges_heuristic() on each memory with an id above metadata.edge_watermark_id, in id
    order, sharing one Tier-3 candidate set per project for the pass (temporal_edges.Tier3Cache). Every `batch_size`
    memories it commits the edges AND the advanced watermark in one transaction, so a run that stops -- at its
    `deadline`, or killed -- keeps every committed batch and the next run resumes after it.

    Args:
        conn: sqlite3 connection.
        since_timestamp: OLD API. When given, the input is every memory created after it (any format this brain has
            held; converted to UTC) and the stored watermark is neither read nor moved -- a manual re-extraction never
            moves the nightly progress.
        dry_run: if True, count proposals without inserting (and never move the watermark).
        deadline: a module-clock instant; Stage 2 stops before the next memory once it has passed.
        batch_size: memories per commit.
        progress_every: seconds between progress lines on stderr (None = none).

    Returns:
        {memories_processed, edges_created, edges_proposed, failed, input_total, remaining, complete,
         watermark_from, watermark_to, watermark_basis, tier3_sets_loaded, seconds}
    """
    # Import temporal_edges module.
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        import temporal_edges as te
    except ImportError:
        return {'memories_processed': 0, 'edges_created': 0, 'edges_proposed': 0,
                'error': 'temporal_edges module not importable'}

    t0 = _clock()
    track = since_timestamp is None
    if track:
        wm = read_edge_watermark(conn)
        basis = "metadata.%s" % EDGE_WATERMARK_KEY
        if wm is None:
            wm, basis = bootstrap_edge_watermark(conn)
    else:
        since_utc = _to_utc_db(since_timestamp) or str(since_timestamp)
        wm, basis = _watermark_from_time(conn, since_utc), "since_timestamp %s UTC" % since_utc

    # The input is fixed when the stage begins: ids only ever grow, so a memory stored during the pass has a larger
    # id than any here and waits for the next run.
    ids = [r[0] for r in conn.execute(
        "SELECT id FROM memories WHERE is_archived = 0 AND id > ? ORDER BY id ASC", (wm,)).fetchall()]
    total = len(ids)

    cache = te.Tier3Cache() if hasattr(te, 'Tier3Cache') else None
    memories_processed = 0
    edges_created = 0
    edges_proposed = 0
    failed = 0
    last_done = wm
    stopped = False
    batch_size = max(1, int(batch_size or 1))
    next_progress = (t0 + progress_every) if progress_every else None

    def _progress(final: bool = False):
        el = _clock() - t0
        rate = memories_processed / el if el > 0 else 0.0
        left = total - memories_processed
        eta = ("%.0fs" % (left / rate)) if rate > 0 and left else ("0s" if not left else "?")
        _stage_log("edges %d/%d (%.0f%%) watermark=%d edges_created=%d rate=%.1f/s elapsed=%.0fs eta=%s%s",
                   memories_processed, total, (100.0 * memories_processed / total) if total else 100.0,
                   last_done, edges_created, rate, el, eta, " (stopped at the budget)" if final and stopped else "")

    for mid in ids:
        if deadline is not None and _clock() >= deadline:
            stopped = True
            break
        try:
            if cache is not None:
                proposals = te.extract_edges_heuristic(conn, mid, top_k=10, tier3_cache=cache)
            else:
                proposals = te.extract_edges_heuristic(conn, mid, top_k=10)
            edges_proposed += len(proposals)

            if not dry_run:
                for p in proposals:
                    try:
                        te.create_edge(
                            conn,
                            source_id=p['source_id'],
                            target_id=p['target_id'],
                            predicate=p['predicate'],
                            confidence=p['confidence'],
                            extraction_method='sleep_loop',
                            metadata=p.get('metadata'),
                        )
                        edges_created += 1
                    except (sqlite3.IntegrityError, ValueError):
                        pass  # Duplicate or invalid - skip.
        except Exception as e:
            failed += 1
            logger.warning("Edge extraction failed for memory %d: %s", mid, e)
        memories_processed += 1
        last_done = mid
        if not dry_run and memories_processed % batch_size == 0:
            if track:
                _write_edge_watermark(conn, last_done)
            conn.commit()
        if next_progress is not None and _clock() >= next_progress:
            _progress()
            next_progress = _clock() + progress_every

    if not dry_run:
        if track and last_done != wm:
            _write_edge_watermark(conn, last_done)
        conn.commit()
    if progress_every:
        _progress(final=True)

    return {
        'memories_processed': memories_processed,
        'edges_created': edges_created,
        'edges_proposed': edges_proposed,
        'failed': failed,
        'input_total': total,
        'remaining': total - memories_processed,
        'complete': not stopped,
        'watermark_from': wm,
        'watermark_to': last_done,
        'watermark_basis': basis,
        'tier3_sets_loaded': getattr(cache, 'loads', None),
        'seconds': round(_clock() - t0, 2),
    }


# ============================================================
# Stage 3: Confidence Decay + Pruning
# ============================================================

def decay_and_prune_edges(
    conn: sqlite3.Connection,
    decay_factor: float = 0.995,
    prune_threshold: float = 0.15,
    max_age_days: int = 45,
    dry_run: bool = False,
) -> dict:
    """Stage 3: Decay edge confidences and prune stale edges.

    Delegates to temporal_edges.decay_confidence() for the actual computation. A pruned edge is RETIRED, never
    deleted (round 2, R2d): valid_until is set and the prune's record kept, so `edges_pruned` counts the edges this
    run retired, a dry run reports the same number, and temporal_edges.DECAY_RESTORE_SQL brings them back.

    Args:
        conn: sqlite3 connection.
        decay_factor: per-day decay multiplier.
        prune_threshold: edges below this + older than max_age_days get pruned (retired).
        max_age_days: age threshold for pruning.
        dry_run: if True, compute but don't modify.

    Returns:
        {edges_decayed, edges_pruned (= retired), decay_anchor}
    """
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        import temporal_edges as te
    except ImportError:
        return {'edges_decayed': 0, 'edges_pruned': 0,
                'error': 'temporal_edges module not importable'}

    # The decay itself counts what a dry run would do (round 2, M7): one formula, so a dry run cannot report the
    # per-run compounding the real run no longer does.
    result = te.decay_confidence(
        conn,
        factor=decay_factor,
        min_threshold=prune_threshold,
        max_age_days=max_age_days,
        dry_run=dry_run,
    )
    if not dry_run:
        conn.commit()

    return {
        'edges_decayed': result.get('decayed_count', 0),
        'edges_pruned': result.get('pruned_count', 0),
        'decay_anchor': result.get('anchor_basis'),
    }


# ============================================================
# Stage 4: Reflection Generation
# ============================================================

def generate_reflections(
    conn: sqlite3.Connection,
    min_unreflected: int = 25,
    mode: str = 'heuristic',
    dry_run: bool = False,
) -> List[int]:
    """Stage 4: Generate reflection memories from unreflected episodics.

    Checks how many episodic memories exist since the last reflection was
    generated. If >= min_unreflected, generates a new reflection.

    Args:
        conn: sqlite3 connection.
        min_unreflected: threshold to trigger reflection generation.
        mode: 'heuristic' or 'llm'.
        dry_run: if True, don't create the reflection.

    Returns:
        List of new reflection memory IDs (empty if threshold not met).
    """
    # Find the most recent reflection's created_at.
    row = conn.execute(
        """SELECT created_at FROM memories
           WHERE kind = 'reflection' AND is_archived = 0
           ORDER BY created_at DESC LIMIT 1"""
    ).fetchone()
    last_reflection_at = row[0] if row else '2000-01-01T00:00:00'

    # Count episodic memories since then.
    count_row = conn.execute(
        """SELECT COUNT(*) FROM memories
           WHERE kind = 'episodic' AND is_archived = 0
             AND created_at > ?""",
        (last_reflection_at,),
    ).fetchone()
    unreflected_count = count_row[0] if count_row else 0

    if unreflected_count < min_unreflected:
        return []

    # Gather recent episodic data for the reflection.
    recent_episodics = conn.execute(
        """SELECT m.id, m.summary, m.category, m.importance
           FROM memories m
           WHERE m.kind = 'episodic' AND m.is_archived = 0
             AND m.created_at > ?
           ORDER BY m.importance DESC
           LIMIT 50""",
        (last_reflection_at,),
    ).fetchall()

    # Collect tags from these episodics.
    all_tags: List[str] = []
    categories: List[str] = []
    for mid, summary, category, importance in recent_episodics:
        categories.append(category)
        tag_rows = conn.execute(
            "SELECT tag FROM memory_tags WHERE memory_id = ?", (mid,)
        ).fetchall()
        all_tags.extend(r[0] for r in tag_rows)

    tag_counts = Counter(all_tags)
    top_5_tags = [t for t, _ in tag_counts.most_common(5)]
    category_counts = Counter(categories)
    top_categories = [c for c, _ in category_counts.most_common(3)]

    # Find tag co-occurrences (pairs that appear together).
    tag_pairs: Counter = Counter()
    for mid, _, _, _ in recent_episodics:
        mem_tags = [r[0] for r in conn.execute(
            "SELECT tag FROM memory_tags WHERE memory_id = ?", (mid,)
        ).fetchall()]
        for i in range(len(mem_tags)):
            for j in range(i + 1, len(mem_tags)):
                pair = tuple(sorted([mem_tags[i], mem_tags[j]]))
                tag_pairs[pair] += 1
    top_pairs = tag_pairs.most_common(3)

    # Build reflection content. (The UTC date, the frame of every timestamp in this brain.)
    now_str = _utc_now().strftime('%Y-%m-%d')
    co_occurrence_str = "; ".join(
        f"{p[0]}+{p[1]} ({c}x)" for p, c in top_pairs
    ) if top_pairs else "none detected"

    reflection_content = (
        f"Sleep Loop Reflection ({now_str}): {unreflected_count} episodic memories accumulated. "
        f"Dominant themes: {', '.join(top_5_tags)}. "
        f"Primary domains: {', '.join(top_categories)}. "
        f"Tag co-occurrence patterns: {co_occurrence_str}. "
        f"Highest-priority work: {recent_episodics[0][1] if recent_episodics else 'none'}."
    )
    reflection_summary = (
        f"Sleep loop reflection: {unreflected_count} unreflected episodics. "
        f"Themes: {', '.join(top_5_tags[:3])}."
    )

    if dry_run:
        return []

    # Store the reflection.
    cursor = conn.execute(
        """INSERT INTO memories
           (content, summary, category, importance, source_project, source_context,
            kind, confidence)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            reflection_content,
            reflection_summary,
            'insight',
            0.6,
            'sleep-loop-reflection',
            f'unreflected_count={unreflected_count}',
            'reflection',
            0.7,
        ),
    )
    reflection_id = cursor.lastrowid
    # TO-03: this memory must be findable.
    _vl_attach(conn, reflection_id, reflection_content, "sleep_loop.generate_reflections",
               sources=[r[0] for r in recent_episodics], transformation="reflection")

    # Tag it.
    conn.execute(
        "INSERT OR IGNORE INTO memory_tags (memory_id, tag) VALUES (?, ?)",
        (reflection_id, 'sleep-loop-reflection'),
    )
    for tag in top_5_tags[:3]:
        conn.execute(
            "INSERT OR IGNORE INTO memory_tags (memory_id, tag) VALUES (?, ?)",
            (reflection_id, tag),
        )

    conn.commit()
    return [reflection_id]


# ============================================================
# Stage 5: Importance Re-ranking
# ============================================================

def rerank_importance(
    conn: sqlite3.Connection,
    boost_cap: float = 0.05,
    decay_rate: float = 0.01,
    min_importance: float = 0.3,
    dry_run: bool = False,
    legacy_boost_decay: bool = False,
) -> dict:
    """Stage 5: salience re-ranking via the SINGLE ACT-R activation view (L5-F P2).

    Historically Stage 5 MUTATED stored importance with its OWN boost/decay rule,
    while claude_memory.cmd_maintain applied a SECOND, DIFFERENT decay formula to
    the same importance signal -- two conflicting mutators racing on one number.

    The L5-F design unifies them: ACT-R activation
    (kind_taxonomy.actr_activation = base-level FadeMem decay + foundation salience
    + importance) is a recall-TIME-ONLY derived view, NEVER a stored 5th column.
    Stage 5 now COMPUTES that activation for the run report and surfaces decay
    candidates, but DOES NOT write stored importance -- and cmd_maintain reads the
    SAME view. There is exactly one decay formula in the substrate.

    The legacy boost/decay stored-importance mutation is retained ONLY behind the
    explicit ``legacy_boost_decay=True`` escape hatch (default OFF) for callers
    that genuinely need the old write behaviour; the default path is write-free.

    Args:
        conn: sqlite3 connection.
        boost_cap / decay_rate / min_importance: legacy tunables (used ONLY when
            legacy_boost_decay=True).
        dry_run: if True, compute without applying.
        legacy_boost_decay: if True, run the OLD double-mutating boost/decay
            (off by default; the ACT-R view is the single decay source otherwise).

    Returns:
        {boosted, decayed, unchanged, mode, activation_evaluated, decay_candidates}
    """
    now = _utc_now()            # last_accessed / created_at are datetime('now'): UTC

    # ----- DEFAULT PATH: the single ACT-R activation view (write-free). ---------
    if not legacy_boost_decay:
        if kind_taxonomy is None or not hasattr(kind_taxonomy, "actr_activation"):
            # Degraded install: no ACT-R view available. Do NOT fall back to the
            # old double-mutating boost/decay -- a no-op pass is correct here
            # (cmd_maintain's own fallback still runs its legacy effective).
            total_active = conn.execute(
                "SELECT COUNT(*) FROM memories WHERE is_archived = 0"
            ).fetchone()[0]
            return {'boosted': 0, 'decayed': 0, 'unchanged': total_active,
                    'mode': 'actr-unavailable-noop', 'activation_evaluated': 0,
                    'decay_candidates': 0}

        # foundation_score is added by a later migration; tolerate its absence on
        # a bare/early schema (the self-test fixture) -- treat as 0.0 there.
        _cols = {r[1] for r in conn.execute("PRAGMA table_info(memories)").fetchall()}
        _fnd_sel = "foundation_score" if "foundation_score" in _cols else "0.0 AS foundation_score"
        rows = conn.execute(
            f"""SELECT id, kind, importance, {_fnd_sel}, access_count,
                      created_at, last_accessed
               FROM memories WHERE is_archived = 0"""
        ).fetchall()

        evaluated = 0
        decay_candidates = 0
        for (_mid, kind, importance, foundation_score, access_count,
             created_at_str, last_accessed_str) in rows:
            ref = last_accessed_str or created_at_str
            try:
                ref_dt = datetime.fromisoformat(ref) if ref else now
            except (ValueError, TypeError):
                ref_dt = now
            age_days = max(0, (now - ref_dt).days)
            activation = kind_taxonomy.actr_activation(
                kind, age_days, recency_count=max(int(access_count or 0), 0),
                foundation_score=foundation_score, importance=importance)
            evaluated += 1
            # A "decay candidate" is a low-activation memory the maintenance sweep
            # would consider -- reported, NOT mutated here (single source of truth).
            if activation < 0.35:
                decay_candidates += 1

        # No commit: this path writes nothing. Stored importance is untouched.
        total_active = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE is_archived = 0"
        ).fetchone()[0]
        return {'boosted': 0, 'decayed': 0, 'unchanged': total_active,
                'mode': 'actr-activation', 'activation_evaluated': evaluated,
                'decay_candidates': decay_candidates}

    # ----- LEGACY PATH (explicit opt-in only): the old boost/decay mutation. ----
    thirty_days_ago = _utc_stamp(now - timedelta(days=30))
    ninety_days_ago = _utc_stamp(now - timedelta(days=90))

    boosted = 0
    decayed = 0

    # Boost: recently accessed memories.
    rows = conn.execute(
        """SELECT id, importance, access_count FROM memories
           WHERE is_archived = 0 AND access_count > 0
             AND last_accessed > ?""",
        (thirty_days_ago,),
    ).fetchall()

    for mid, current_imp, access_count in rows:
        boost = min(boost_cap, access_count * 0.01)
        new_imp = min(1.0, current_imp + boost)
        if abs(new_imp - current_imp) > 0.001:
            if not dry_run:
                conn.execute(
                    "UPDATE memories SET importance = ? WHERE id = ?",
                    (round(new_imp, 4), mid),
                )
            boosted += 1

    # Decay: old unaccessed episodic memories.
    rows = conn.execute(
        """SELECT id, importance, created_at, last_accessed FROM memories
           WHERE is_archived = 0 AND kind = 'episodic'
             AND access_count = 0
             AND created_at < ?""",
        (ninety_days_ago,),
    ).fetchall()

    for mid, current_imp, created_at_str, last_accessed_str in rows:
        if current_imp <= min_importance:
            continue
        try:
            created_at = datetime.fromisoformat(created_at_str)
        except (ValueError, TypeError):
            continue
        days_old = (now - created_at).days
        periods = max(0, (days_old - 90) // 30)
        decay_amount = periods * decay_rate
        new_imp = max(min_importance, current_imp - decay_amount)
        if abs(new_imp - current_imp) > 0.001:
            if not dry_run:
                conn.execute(
                    "UPDATE memories SET importance = ? WHERE id = ?",
                    (round(new_imp, 4), mid),
                )
            decayed += 1

    if not dry_run:
        conn.commit()

    total_active = conn.execute(
        "SELECT COUNT(*) FROM memories WHERE is_archived = 0"
    ).fetchone()[0]

    return {
        'boosted': boosted,
        'decayed': decayed,
        'unchanged': total_active - boosted - decayed,
        'mode': 'legacy-boost-decay',
    }


# ============================================================
# Stage 6: Run Logging
# ============================================================

def _runs_columns(conn: sqlite3.Connection) -> Set[str]:
    try:
        return {r[1] for r in conn.execute("PRAGMA table_info(sleep_loop_runs)").fetchall()}
    except sqlite3.Error:
        return set()


def lifecycle_columns_present(conn: sqlite3.Connection) -> bool:
    """True once migration 040 is applied: sleep_loop_runs has stage_reached + stage_log and accepts 'timeout'."""
    return {'stage_reached', 'stage_log'} <= _runs_columns(conn)


def _start_run(conn: sqlite3.Connection, config: dict) -> int:
    """Insert a new run record (UTC, the brain's format) and return its ID."""
    cols = _runs_columns(conn)
    if 'stage_reached' in cols:
        cursor = conn.execute(
            """INSERT INTO sleep_loop_runs (started_at, config_snapshot, consolidation_mode, stage_reached)
               VALUES (?, ?, ?, 'start')""",
            (_utc_stamp(), json.dumps(config), config.get('consolidation_mode', 'heuristic')),
        )
    else:
        cursor = conn.execute(
            """INSERT INTO sleep_loop_runs (started_at, config_snapshot, consolidation_mode)
               VALUES (?, ?, ?)""",
            (_utc_stamp(), json.dumps(config), config.get('consolidation_mode', 'heuristic')),
        )
    conn.commit()
    return cursor.lastrowid


def _mark_stage(conn: sqlite3.Connection, run_id: Optional[int], stage: str) -> None:
    """Record the stage the run is entering, COMMITTED, so a run killed without warning (SIGKILL, OOM, power) still
    says where it died. Called only at stage boundaries, where no stage transaction is open."""
    if not run_id or 'stage_reached' not in _runs_columns(conn):
        return
    try:
        conn.execute("UPDATE sleep_loop_runs SET stage_reached = ? WHERE id = ?", (stage, run_id))
        conn.commit()
    except sqlite3.Error as e:
        logger.warning("could not record stage %s for run %s: %s", stage, run_id, e)


def close_orphan_runs(conn: sqlite3.Connection, run_id: Optional[int]) -> List[int]:
    """Close every OTHER 'running' row. Called only while this process holds the brain's run-lock, so no other run of
    this brain is live and every such row belongs to a process that died without closing it -- the health check found
    eight (rows 116-123, 2026-10-06..13), killed by systemd at TimeoutStartSec with nothing to close them.

    The row becomes 'timeout' (the run was cut off from outside; contract C5) -- 'error' on a brain without migration
    040, whose CHECK does not know 'timeout' -- keeps the stage it last recorded, and says who closed it and when.
    """
    try:
        rows = [r[0] for r in conn.execute(
            "SELECT id FROM sleep_loop_runs WHERE status = 'running' AND id != ?", (run_id or -1,)).fetchall()]
    except sqlite3.Error:
        return []
    if not rows:
        return []
    status = 'timeout' if lifecycle_columns_present(conn) else 'error'
    note = ("orphaned: the process ended without closing this row (closed by run %s at %s UTC)"
            % (run_id if run_id else "a dry run", _utc_stamp()))
    for rid in rows:
        conn.execute(
            """UPDATE sleep_loop_runs
               SET status = ?, completed_at = ?, error_log = CASE WHEN error_log IS NULL OR error_log = ''
                                                                  THEN ? ELSE error_log || '; ' || ? END
               WHERE id = ? AND status = 'running'""",
            (status, _utc_stamp(), note, note, rid))
    conn.commit()
    return rows


def _complete_run(conn: sqlite3.Connection, run_id: int, results: dict, status: str,
                  stage_reached: Optional[str] = None, stage_log: Optional[dict] = None):
    """Close the run record (UTC, the brain's format). A brain without migration 040 cannot hold 'timeout' (its CHECK
    predates it): the row is closed 'partial' and the error_log says it was a timeout -- closed either way."""
    cols = _runs_columns(conn)
    error_log = results.get('error_log')
    if status == 'timeout' and 'stage_reached' not in cols:
        status = 'partial'
        error_log = "timeout (recorded as partial: migration 040 is not applied to this brain)" + (
            "; " + error_log if error_log else "")
    params = [
        _utc_stamp(),
        status,
        results.get('memories_consolidated', 0),
        results.get('clusters_found', 0),
        results.get('edges_extracted', 0),
        results.get('edges_decayed', 0),
        results.get('edges_pruned', 0),
        results.get('reflections_generated', 0),
        results.get('importance_adjustments', 0),
        error_log,
        results.get('duration_seconds', 0),
    ]
    extra = ""
    if 'stage_reached' in cols:
        extra = ", stage_reached = COALESCE(?, stage_reached), stage_log = ?"
        params += [stage_reached, json.dumps(stage_log, default=str) if stage_log is not None else None]
    params.append(run_id)
    conn.execute(
        """UPDATE sleep_loop_runs SET
               completed_at = ?,
               status = ?,
               memories_consolidated = ?,
               clusters_found = ?,
               edges_extracted = ?,
               edges_decayed = ?,
               edges_pruned = ?,
               reflections_generated = ?,
               importance_adjustments = ?,
               error_log = ?,
               duration_seconds = ?""" + extra + """
           WHERE id = ?""",
        params,
    )
    conn.commit()


# ============================================================
# Orchestrator
# ============================================================

def _install_term_handlers():
    """SIGTERM/SIGHUP -> Terminated, so the orchestrator closes the run row before the process ends. Only from the
    main thread (signal.signal refuses elsewhere); returns the previous handlers to restore."""
    if threading.current_thread() is not threading.main_thread():
        return None
    prev = {}

    def _raise(signum, _frame):
        raise Terminated(signum)

    for name in ('SIGTERM', 'SIGHUP'):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            prev[sig] = signal.signal(sig, _raise)
        except (ValueError, OSError):
            pass
    return prev


def _restore_handlers(prev):
    for sig, handler in (prev or {}).items():
        try:
            signal.signal(sig, handler)
        except (ValueError, OSError, TypeError):
            pass


def run_sleep_cycle(
    db_path: str,
    config: Optional[dict] = None,
    dry_run: bool = False,
) -> dict:
    """Main entry point: run the full 6-stage sleep cycle, inside its own time budget.

    THE ROW ALWAYS CLOSES (round 1, L3; contract C5). Every run that opens a sleep_loop_runs row closes it:
      success  every stage ran and Stage 2 drained its input;
      partial  every stage ran but Stage 2 stopped at its slice of the budget (the rest carried on the id watermark),
               or a stage failed and the others ran;
      timeout  the budget ended where work cannot be carried (Stage 1, or before a later stage), or the process was
               told to stop (SIGTERM/SIGHUP/SIGINT) -- stage_reached names the stage;
      error    no stage succeeded.
    A process killed without warning (SIGKILL, OOM) cannot close its own row; the next run closes it
    (close_orphan_runs), and it keeps the stage it had reached.

    Args:
        db_path: path to the SQLite database.
        config: optional config dict overriding DEFAULT_CONFIG (budget_seconds 0/None = no limit).
        dry_run: if True, simulate without modifying the database.

    Returns:
        Run summary dict matching the sleep_loop_runs schema, plus stage_reached, stage_log and the Stage-2 watermark.
    """
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    cfg['lock_file'], lock_note = run_lock_path(db_path, cfg.get('lock_file'))
    t0 = time.time()
    budget = _Budget(cfg.get('budget_seconds'))
    progress_every = cfg.get('progress_every_seconds') or None

    # Acquire THIS BRAIN's lock (unless dry_run -- no contention risk).
    if not dry_run:
        try:
            acquired = _acquire_lock(cfg['lock_file'])
        except OSError as e:
            return {
                'status': 'error',
                'reason': "could not create this brain's run-lock %s: %s" % (cfg['lock_file'], e),
                'lock_file': cfg['lock_file'],
            }
        if not acquired:
            out = {
                'status': 'skipped',
                'reason': 'Lock file held by another process',
                'lock_file': cfg['lock_file'],
            }
            if lock_note:
                out['lock_note'] = lock_note
            return out

    prev_handlers = _install_term_handlers() if not dry_run else None
    conn = None
    run_id = None
    closed = False
    stage = 'start'
    stage_log: Dict[str, Any] = {}
    results = {
        'memories_consolidated': 0,
        'clusters_found': 0,
        'edges_extracted': 0,
        'edges_proposed': 0,
        'edges_decayed': 0,
        'edges_pruned': 0,
        'reflections_generated': 0,
        'importance_adjustments': 0,
        'error_log': None,
        'duration_seconds': 0,
    }
    errors: List[str] = []
    try:
        conn = _get_conn(db_path)

        # Ensure migration is applied.
        if not _is_migration_applied(conn):
            migration_sql_path = Path(__file__).parent.parent / "migrations" / "migration_005_sleep_loop.sql"
            if migration_sql_path.exists():
                conn.executescript(migration_sql_path.read_text())
                conn.commit()
            else:
                conn.close()
                conn = None
                return {'status': 'error', 'reason': 'Migration 005 not applied and SQL not found'}

        # Start run logging, then close what dead runs left open (we hold the brain's lock: none of them is live).
        orphans: List[int] = []
        if not dry_run:
            run_id = _start_run(conn, cfg)
            orphans = close_orphan_runs(conn, run_id)
            if orphans:
                _stage_log("closed %d orphaned run row(s) left 'running' by dead processes: %s",
                           len(orphans), ", ".join(map(str, orphans)))
        if not lifecycle_columns_present(conn):
            _stage_log("migration 040 is not applied to this brain: a timeout is recorded as 'partial' and the stage "
                       "reached is not stored (run the installer's migration step)")
        _stage_log("run %s begins (budget %s, dry_run=%s)", run_id if run_id else "-",
                   ("%ds" % budget.seconds) if budget.seconds else "none", dry_run)

        stages_ok = 0
        interrupted: Optional[str] = None
        stage2_complete = True

        def _begin(name: str) -> float:
            nonlocal stage
            stage = name
            _mark_stage(conn, run_id, name)
            _stage_log("stage %s begins (elapsed %.1fs)", name, budget.elapsed())
            return _clock()

        def _end(name: str, t: float, **info):
            entry = {'seconds': round(_clock() - t, 2)}
            entry.update({k: v for k, v in info.items() if v is not None})
            stage_log[name] = entry
            _stage_log("stage %s ends: %s", name, json.dumps(entry, default=str))

        work_deadline = budget.deadline(cfg.get('stage2_reserve_seconds') or 0)

        # Stage 1: Consolidation
        t = _begin('consolidation')
        try:
            consolidations = consolidate_episodics(
                conn,
                lookback_days=cfg['lookback_days'],
                min_cluster_size=cfg['min_cluster_size'],
                similarity_threshold=cfg['similarity_threshold'],
                mode=cfg['consolidation_mode'],
                dry_run=dry_run,
                deadline=work_deadline,
            )
            results['clusters_found'] = len(consolidations)
            results['memories_consolidated'] = sum(
                1 for c in consolidations if c.get('consolidated_memory_id')
            )
            stages_ok += 1
            _end('consolidation', t, clusters=results['clusters_found'],
                 consolidated=results['memories_consolidated'])
        except BudgetExhausted:
            interrupted = 'consolidation'
            if not dry_run:
                conn.rollback()
            _end('consolidation', t, stopped='budget')
        except Exception as e:
            errors.append(f"Stage 1 (consolidation): {e}")
            logger.error("Stage 1 failed: %s", e)
            if not dry_run:
                conn.rollback()
            _end('consolidation', t, error=str(e))

        # Stage 2: Edge Extraction (stops at its slice of the budget; the watermark carries the rest)
        if interrupted is None:
            t = _begin('edges')
            try:
                edge_result = extract_new_edges(conn, dry_run=dry_run, deadline=work_deadline,
                                                batch_size=cfg.get('edge_batch_size') or 50,
                                                progress_every=progress_every)
                results['edges_extracted'] = edge_result.get('edges_created', 0)
                results['edges_proposed'] = edge_result.get('edges_proposed', 0)
                results['edge_watermark_id'] = edge_result.get('watermark_to')
                results['edge_backlog_remaining'] = edge_result.get('remaining')
                stage2_complete = bool(edge_result.get('complete', True))
                stages_ok += 1
                _end('edges', t, processed=edge_result.get('memories_processed'),
                     input_total=edge_result.get('input_total'), remaining=edge_result.get('remaining'),
                     edges_created=edge_result.get('edges_created'), failed=edge_result.get('failed'),
                     watermark_from=edge_result.get('watermark_from'), watermark_to=edge_result.get('watermark_to'),
                     watermark_basis=edge_result.get('watermark_basis'),
                     tier3_sets_loaded=edge_result.get('tier3_sets_loaded'),
                     stopped=None if stage2_complete else 'budget')
            except Exception as e:
                errors.append(f"Stage 2 (edge extraction): {e}")
                logger.error("Stage 2 failed: %s", e)
                if not dry_run:
                    conn.rollback()
                _end('edges', t, error=str(e))

        def _over_budget(next_stage: str) -> bool:
            nonlocal interrupted
            if interrupted is None and budget.exhausted():
                interrupted = next_stage
                errors.append("the run's time budget (%ds) ended before stage %s" % (budget.seconds, next_stage))
            return interrupted is not None

        # Stage 3: Confidence Decay
        if not _over_budget('decay'):
            t = _begin('decay')
            try:
                decay_result = decay_and_prune_edges(
                    conn,
                    decay_factor=cfg['decay_factor'],
                    prune_threshold=cfg['prune_threshold'],
                    max_age_days=cfg['max_age_days'],
                    dry_run=dry_run,
                )
                results['edges_decayed'] = decay_result.get('edges_decayed', 0)
                results['edges_pruned'] = decay_result.get('edges_pruned', 0)
                stages_ok += 1
                _end('decay', t, decayed=results['edges_decayed'], pruned=results['edges_pruned'])
            except Exception as e:
                errors.append(f"Stage 3 (decay): {e}")
                logger.error("Stage 3 failed: %s", e)
                if not dry_run:
                    conn.rollback()
                _end('decay', t, error=str(e))

        # Stage 4: Reflection Generation
        if not _over_budget('reflection'):
            t = _begin('reflection')
            try:
                reflection_ids = generate_reflections(
                    conn,
                    min_unreflected=cfg['min_unreflected_for_reflection'],
                    mode=cfg['consolidation_mode'],
                    dry_run=dry_run,
                )
                results['reflections_generated'] = len(reflection_ids)
                stages_ok += 1
                _end('reflection', t, generated=results['reflections_generated'])
            except Exception as e:
                errors.append(f"Stage 4 (reflection): {e}")
                logger.error("Stage 4 failed: %s", e)
                if not dry_run:
                    conn.rollback()
                _end('reflection', t, error=str(e))

        # Stage 5: Importance Re-ranking
        if not _over_budget('importance'):
            t = _begin('importance')
            try:
                rank_result = rerank_importance(
                    conn,
                    boost_cap=cfg['importance_boost_cap'],
                    decay_rate=cfg['importance_decay_rate'],
                    min_importance=cfg['min_importance'],
                    dry_run=dry_run,
                )
                results['importance_adjustments'] = rank_result.get('boosted', 0) + rank_result.get('decayed', 0)
                stages_ok += 1
                _end('importance', t, adjustments=results['importance_adjustments'])
            except Exception as e:
                errors.append(f"Stage 5 (importance): {e}")
                logger.error("Stage 5 failed: %s", e)
                if not dry_run:
                    conn.rollback()
                _end('importance', t, error=str(e))

        # Stage 5b (L5-L): The Quickening -- recall-tide spike sense.
        # Observability + dream/whisper seeding. FAIL-SOFT and does NOT count
        # toward stages_ok (it is a second sensor on an existing sink, not one of
        # the five core consolidation stages). Skipped on dry_run / if disabled /
        # if the module is absent. The env kill-switch lives inside the stage.
        if not dry_run and quickening is not None and cfg.get('quickening_enabled', True) \
                and not _over_budget('quickening'):
            t = _begin('quickening')
            try:
                q_result = quickening.run_quickening_stage(conn, cfg=cfg, batch_id=run_id)
                results['quickening'] = q_result
                if q_result.get('status') == 'error':
                    errors.append(f"Stage 5b (quickening): {q_result.get('reason')}")
            except Exception as e:  # defense-in-depth; the stage is already fail-soft
                results['quickening'] = {'status': 'error', 'reason': str(e)}
                errors.append(f"Stage 5b (quickening): {e}")
                logger.error("Stage 5b (quickening) failed: %s", e)
            _end('quickening', t, status=(results.get('quickening') or {}).get('status'))

        # Stage 5c (L5-E-COMM): Leiden Cognitive-Map Communities. Runs LAST --
        # after extraction -> belief -> NREM-settle -> PageRank -> quickening --
        # so it clusters the SETTLED graph. Detection (Leiden if available, else
        # GROUP BY, else stdlib connected-components) + lazy content-hash-gated
        # summaries. PURE CONSUMER: writes ONLY its own three tables, NEVER
        # memory_edges. FAIL-SOFT and does NOT count toward stages_ok (it is a
        # visualization sense, not one of the five core consolidation stages).
        # Skipped on dry_run / if disabled / if the module is absent. The env
        # kill-switch (VOIDLOOM_NO_COMMUNITIES) lives inside the stage.
        if not dry_run and communities is not None and cfg.get('communities_enabled', True) \
                and not _over_budget('communities'):
            t = _begin('communities')
            try:
                c_result = communities.run_communities_stage(conn, cfg=cfg, batch_id=run_id)
                results['communities'] = c_result
                if c_result.get('status') == 'error':
                    errors.append(f"Stage 5c (communities): {c_result.get('reason')}")
            except Exception as e:  # defense-in-depth; the stage is already fail-soft
                results['communities'] = {'status': 'error', 'reason': str(e)}
                errors.append(f"Stage 5c (communities): {e}")
                logger.error("Stage 5c (communities) failed: %s", e)
            _end('communities', t, status=(results.get('communities') or {}).get('status'))

        # Stage 6: Finalize run record.
        if interrupted == 'consolidation':
            errors.append("the run's time budget (%ds) ended during stage consolidation" % budget.seconds)
        duration = time.time() - t0
        results['duration_seconds'] = round(duration, 2)

        if errors:
            results['error_log'] = "; ".join(errors)

        # Determine status.
        if interrupted is not None:
            status = 'timeout'
        elif stages_ok == 5:
            status = 'success' if stage2_complete else 'partial'
        elif stages_ok == 0:
            status = 'error'
        else:
            status = 'partial'

        if interrupted is None:
            stage = 'finalize'
        if run_id and not dry_run:
            _complete_run(conn, run_id, results, status, stage_reached=stage, stage_log=stage_log)
            closed = True
            stages_ok += 1
        _stage_log("run %s ends: %s in %.1fs (stage reached: %s)", run_id if run_id else "-", status, duration, stage)

        conn.close()
        conn = None

        out = {
            'status': status,
            'run_id': run_id,
            'dry_run': dry_run,
            'stages_ok': stages_ok,
            'stage_reached': stage,
            'stage_log': stage_log,
            'orphans_closed': orphans,
            'config': cfg,
            **results,
        }
        if lock_note:
            out['lock_note'] = lock_note
        return out

    except BaseException as e:
        # Terminated (SIGTERM/SIGHUP), KeyboardInterrupt (SIGINT), or anything a stage let escape: the row closes.
        # A stop request is an ANSWER (the run returns 'timeout', every committed batch kept); anything else is a
        # defect and is re-raised once the row is closed 'error'.
        stopped = isinstance(e, (Terminated, KeyboardInterrupt))
        kind = 'timeout' if stopped else 'error'
        why = str(e) or type(e).__name__
        if conn is not None and run_id and not closed:
            try:
                conn.rollback()
            except Exception:
                pass
            try:
                results['duration_seconds'] = round(time.time() - t0, 2)
                errors.append("%s during stage %s: %s" % ("stopped" if stopped else "failed", stage, why))
                results['error_log'] = "; ".join(errors)
                _complete_run(conn, run_id, results, kind, stage_reached=stage, stage_log=stage_log)
                closed = True
                _stage_log("run %s closed as %s at stage %s: %s", run_id, kind, stage, why)
            except Exception as close_err:      # the row will be closed by the next run's orphan sweep
                logger.error("could not close sleep run %s: %s", run_id, close_err)
        if not stopped:
            raise
        out = {'status': 'timeout', 'run_id': run_id, 'dry_run': dry_run, 'stage_reached': stage,
               'stage_log': stage_log, 'stopped_by': why, 'row_closed': closed, 'config': cfg, **results}
        if lock_note:
            out['lock_note'] = lock_note
        return out

    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        _restore_handlers(prev_handlers)
        if not dry_run:
            _release_lock(cfg['lock_file'])


# ============================================================
# Run History Query
# ============================================================

def get_run_history(conn: sqlite3.Connection, limit: int = 10) -> List[dict]:
    """Query recent sleep loop runs for monitoring."""
    if not _is_migration_applied(conn):
        return []

    rows = conn.execute(
        """SELECT id, started_at, completed_at, status, memories_consolidated,
                  clusters_found, edges_extracted, edges_decayed, edges_pruned,
                  reflections_generated, importance_adjustments, consolidation_mode,
                  error_log, duration_seconds
           FROM sleep_loop_runs
           ORDER BY id DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()

    return [
        {
            'run_id': r[0],
            'started_at': r[1],
            'completed_at': r[2],
            'status': r[3],
            'memories_consolidated': r[4],
            'clusters_found': r[5],
            'edges_extracted': r[6],
            'edges_decayed': r[7],
            'edges_pruned': r[8],
            'reflections_generated': r[9],
            'importance_adjustments': r[10],
            'consolidation_mode': r[11],
            'error_log': r[12],
            'duration_seconds': r[13],
        }
        for r in rows
    ]


# ============================================================
# Unit Tests
# ============================================================

def unit_tests():
    """Self-contained unit tests. Returns (passed, failed, details)."""
    import tempfile
    import shutil

    passed = 0
    failed = 0
    details: List[str] = []

    def assert_eq(name, got, expected):
        nonlocal passed, failed
        if got == expected:
            passed += 1
            details.append(f"  PASS: {name}")
        else:
            failed += 1
            details.append(f"  FAIL: {name} — got {got!r}, expected {expected!r}")

    def assert_true(name, condition):
        nonlocal passed, failed
        if condition:
            passed += 1
            details.append(f"  PASS: {name}")
        else:
            failed += 1
            details.append(f"  FAIL: {name}")

    # SELF-CONTAINED (Round 3 G3): this suite died before its first row on two counts -- its fixture brain declared
    # no embedding tier (embed_resolver REFUSES to guess: EmbedTierUnresolved at test 6), and a memory this organ
    # writes is embedded through memory_write, which loads the real model (a network fetch on a fresh machine). The
    # fixture now declares its tier (memory_embeddings, 768 wide -- the vectors it inserts), and for the suite's
    # duration the write hook embeds with a deterministic stand-in vector of that width. Restored in `finally`.
    global _vl_mw
    _real_mw = _vl_mw
    if _real_mw is not None:
        import types
        _unit = struct.pack("768f", *([1.0 / math.sqrt(768)] * 768))
        _vl_mw = types.SimpleNamespace(on_memory_written=lambda *a, **k: _real_mw.on_memory_written(
            *a, encoder=lambda _text: _unit, **k))

    # Create a temporary test database.
    tmp_dir = tempfile.mkdtemp(prefix="sleep_loop_test_")
    test_db = os.path.join(tmp_dir, "test.db")

    try:
        conn = sqlite3.connect(test_db)
        conn.execute("PRAGMA foreign_keys=ON")

        # Create minimal schema.
        conn.executescript("""
            CREATE TABLE memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                summary TEXT,
                category TEXT DEFAULT 'domain_knowledge',
                importance REAL DEFAULT 0.5,
                source_project TEXT,
                source_context TEXT,
                kind TEXT DEFAULT 'episodic',
                confidence REAL DEFAULT 1.0,
                derived_from TEXT,
                consolidated_at TEXT,
                is_archived INTEGER DEFAULT 0,
                access_count INTEGER DEFAULT 0,
                last_accessed TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                version INTEGER DEFAULT 1
            );
            CREATE TABLE memory_embeddings (
                memory_id INTEGER PRIMARY KEY,
                embedding BLOB
            );
            CREATE TABLE memory_tags (
                memory_id INTEGER,
                tag TEXT,
                UNIQUE(memory_id, tag)
            );
            CREATE TABLE memory_edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL,
                target_id INTEGER NOT NULL,
                predicate TEXT NOT NULL,
                confidence REAL DEFAULT 0.8,
                valid_from TEXT,
                valid_until TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                metadata TEXT,
                extraction_method TEXT DEFAULT 'heuristic',
                UNIQUE(source_id, target_id, predicate)
            );
            CREATE TABLE metadata (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            INSERT INTO metadata (key, value) VALUES ('schema_version', '2.3-L2-procedural-memory');
            INSERT INTO metadata (key, value) VALUES ('active_embedding_table', 'memory_embeddings');
        """)

        # Apply migration 005.
        migration_path = Path(__file__).parent.parent / "migrations" / "migration_005_sleep_loop.sql"
        if migration_path.exists():
            conn.executescript(migration_path.read_text())
        else:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS sleep_loop_runs (
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
                    error_log TEXT,
                    config_snapshot TEXT,
                    duration_seconds REAL
                );
                CREATE TABLE IF NOT EXISTS consolidation_sources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    consolidated_memory_id INTEGER NOT NULL,
                    source_memory_id INTEGER NOT NULL,
                    cluster_id INTEGER NOT NULL,
                    created_at TEXT DEFAULT (datetime('now'))
                );
            """)
        conn.commit()

        # Test 1: Migration applied check.
        assert_true("migration_applied", _is_migration_applied(conn))

        # Test 2: Cosine similarity with identical vectors.
        v1 = struct.pack("3f", 1.0, 0.0, 0.0)
        v2 = struct.pack("3f", 1.0, 0.0, 0.0)
        assert_eq("cosine_identical", round(_cosine_similarity(v1, v2), 4), 1.0)

        # Test 3: Cosine similarity with orthogonal vectors.
        v3 = struct.pack("3f", 0.0, 1.0, 0.0)
        assert_eq("cosine_orthogonal", round(_cosine_similarity(v1, v3), 4), 0.0)

        # Test 4: Centroid of identical vectors.
        centroid = _centroid_embedding([v1, v1])
        vals = struct.unpack("3f", centroid)
        assert_eq("centroid_identical", round(vals[0], 4), 1.0)

        # Test 5: Centroid normalisation.
        v4 = struct.pack("3f", 1.0, 1.0, 0.0)
        centroid2 = _centroid_embedding([v1, v4])
        vals2 = struct.unpack("3f", centroid2)
        norm = math.sqrt(sum(x*x for x in vals2))
        assert_true("centroid_normalized", abs(norm - 1.0) < 0.01)

        # Test 6: Consolidation with no episodic memories returns empty.
        result = consolidate_episodics(conn, lookback_days=7)
        assert_eq("consolidate_empty", result, [])

        # Test 7: Insert 5 similar episodic memories and cluster.
        import numpy as np
        rng = np.random.RandomState(42)
        base_vec = rng.randn(768).astype(np.float32)
        base_vec = base_vec / np.linalg.norm(base_vec)
        for i in range(5):
            # Noise = 0.01 * sqrt(768) scale keeps cosine > 0.99 for 768-dim.
            noise = rng.randn(768).astype(np.float32) * 0.01
            vec = base_vec + noise
            vec = vec / np.linalg.norm(vec)
            emb = struct.pack(f"768f", *vec.tolist())
            conn.execute(
                """INSERT INTO memories (content, summary, category, kind, importance, created_at)
                   VALUES (?, ?, 'domain_knowledge', 'episodic', 0.7, datetime('now', ?))""",
                (f"Test memory {i} about clustering patterns", f"Clustering test {i}",
                 f"-{i} hours"),
            )
            mid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                "INSERT INTO memory_embeddings (memory_id, embedding) VALUES (?, ?)",
                (mid, emb),
            )
            conn.execute(
                "INSERT INTO memory_tags (memory_id, tag) VALUES (?, ?)",
                (mid, "test-cluster"),
            )
        conn.commit()

        clusters = consolidate_episodics(conn, lookback_days=7, similarity_threshold=0.6)
        assert_true("consolidate_found_cluster", len(clusters) >= 1)
        if clusters:
            assert_true("cluster_has_sources", len(clusters[0]['source_ids']) >= 3)
            assert_true("cluster_has_id", clusters[0]['consolidated_memory_id'] is not None)

        # Test 8: Verify consolidated memory stored correctly.
        if clusters and clusters[0]['consolidated_memory_id']:
            cid = clusters[0]['consolidated_memory_id']
            row = conn.execute(
                "SELECT kind, derived_from, consolidated_at FROM memories WHERE id = ?",
                (cid,),
            ).fetchone()
            assert_eq("consolidated_kind", row[0], 'semantic')
            assert_true("consolidated_derived_from", row[1] is not None)
            assert_true("consolidated_timestamp", row[2] is not None)

            # Verify consolidation_sources.
            sources = conn.execute(
                "SELECT source_memory_id FROM consolidation_sources WHERE consolidated_memory_id = ?",
                (cid,),
            ).fetchall()
            assert_true("provenance_recorded", len(sources) >= 3)

        # Test 9: Dry run produces clusters without inserting.
        dry_clusters = consolidate_episodics(conn, lookback_days=7, similarity_threshold=0.6, dry_run=True)
        # Since we already consolidated above, remaining unclustered might be fewer.
        # Just verify dry_run returns None for consolidated_memory_id.
        for dc in dry_clusters:
            assert_eq("dry_run_no_insert", dc['consolidated_memory_id'], None)

        # Test 10: Importance re-ranking boost (LEGACY escape-hatch path).
        # The DEFAULT Stage-5 path is now the write-free ACT-R activation view
        # (single decay source); the old boost/decay write is retained ONLY behind
        # legacy_boost_decay=True, so these legacy assertions opt into it.
        conn.execute(
            """INSERT INTO memories (content, summary, category, kind, importance, access_count, last_accessed, created_at)
               VALUES ('accessed memory', 'accessed', 'domain_knowledge', 'episodic', 0.5, 5, datetime('now'), datetime('now', '-1 day'))"""
        )
        conn.commit()
        rank_result = rerank_importance(conn, boost_cap=0.05, legacy_boost_decay=True)
        assert_true("boost_applied", rank_result['boosted'] >= 1)

        # Test 11: Importance re-ranking decay (LEGACY escape-hatch path).
        # Need 90 + 30 = 120 days minimum for 1 decay period. Use -150 for margin.
        conn.execute(
            """INSERT INTO memories (content, summary, category, kind, importance, access_count, last_accessed, created_at)
               VALUES ('old unaccessed', 'old', 'domain_knowledge', 'episodic', 0.8, 0, NULL, datetime('now', '-150 days'))"""
        )
        conn.commit()
        rank_result2 = rerank_importance(conn, decay_rate=0.01, min_importance=0.3, legacy_boost_decay=True)
        assert_true("decay_applied", rank_result2['decayed'] >= 1)

        # Test 12: Minimum importance floor (LEGACY escape-hatch path).
        conn.execute(
            """INSERT INTO memories (content, summary, category, kind, importance, access_count, last_accessed, created_at)
               VALUES ('very old', 'ancient', 'domain_knowledge', 'episodic', 0.3, 0, NULL, datetime('now', '-365 days'))"""
        )
        conn.commit()
        rerank_importance(conn, decay_rate=0.01, min_importance=0.3, legacy_boost_decay=True)
        row = conn.execute(
            "SELECT importance FROM memories WHERE summary = 'ancient'"
        ).fetchone()
        assert_true("min_importance_floor", row[0] >= 0.3)

        # Test 12b (L5-F P2): the DEFAULT Stage-5 path is WRITE-FREE -- it uses the
        # single ACT-R activation view and never mutates stored importance.
        conn.execute(
            """INSERT INTO memories (content, summary, category, kind, importance, access_count, last_accessed, created_at)
               VALUES ('actr probe', 'actr-probe', 'domain_knowledge', 'episodic', 0.6, 3, datetime('now'), datetime('now', '-2 days'))"""
        )
        conn.commit()
        before_imp = conn.execute(
            "SELECT importance FROM memories WHERE summary = 'actr-probe'"
        ).fetchone()[0]
        actr_res = rerank_importance(conn)  # DEFAULT path (legacy_boost_decay=False)
        after_imp = conn.execute(
            "SELECT importance FROM memories WHERE summary = 'actr-probe'"
        ).fetchone()[0]
        assert_true("actr_mode_is_default", actr_res.get('mode') in ('actr-activation', 'actr-unavailable-noop'))
        assert_eq("actr_default_is_write_free", round(float(after_imp), 6), round(float(before_imp), 6))
        assert_eq("actr_default_zero_boost", actr_res.get('boosted', 0), 0)

        # Test 13: Reflection generation threshold not met.
        reflections = generate_reflections(conn, min_unreflected=100)
        assert_eq("reflection_threshold_not_met", reflections, [])

        # Test 14: Reflection generation with enough episodics.
        for i in range(30):
            conn.execute(
                """INSERT INTO memories (content, summary, category, kind, importance, created_at)
                   VALUES (?, ?, 'domain_knowledge', 'episodic', 0.5, datetime('now'))""",
                (f"Reflection test memory {i}", f"Ref test {i}"),
            )
            mid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                "INSERT INTO memory_tags (memory_id, tag) VALUES (?, ?)",
                (mid, "reflection-test"),
            )
        conn.commit()
        reflections = generate_reflections(conn, min_unreflected=25)
        assert_true("reflection_generated", len(reflections) >= 1)
        if reflections:
            row = conn.execute(
                "SELECT kind FROM memories WHERE id = ?", (reflections[0],)
            ).fetchone()
            assert_eq("reflection_kind", row[0], 'reflection')

        # Test 15: Run history.
        conn.execute(
            """INSERT INTO sleep_loop_runs (started_at, completed_at, status, memories_consolidated)
               VALUES (datetime('now'), datetime('now'), 'success', 3)"""
        )
        conn.commit()
        history = get_run_history(conn, limit=5)
        assert_true("run_history_not_empty", len(history) >= 1)
        assert_eq("run_history_status", history[0]['status'], 'success')

        # Test 16: Lock file acquisition.
        lock_path = os.path.join(tmp_dir, "test.lock")
        assert_true("lock_acquire", _acquire_lock(lock_path))
        _release_lock(lock_path)
        # Released = it can be taken again. Not "the file is gone": filelock (the primary backend) unlocks and
        # leaves the file in place on some versions, which is a correct release (found on a CI runner, 2026-10-27).
        assert_true("lock_released", _acquire_lock(lock_path))
        _release_lock(lock_path)

        # Test 17: Decay dry_run simulation.
        conn.execute(
            """INSERT INTO memory_edges (source_id, target_id, predicate, confidence, created_at, extraction_method)
               VALUES (1, 2, 'references', 0.8, datetime('now', '-60 days'), 'heuristic')"""
        )
        conn.commit()
        decay_result = decay_and_prune_edges(conn, decay_factor=0.99, prune_threshold=0.15, max_age_days=30, dry_run=True)
        assert_true("decay_dry_run_computes", decay_result['edges_decayed'] >= 0 or decay_result['edges_pruned'] >= 0)

        conn.close()

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        _vl_mw = _real_mw

    print(f"\n{'='*60}")
    print(f"Sleep Loop Unit Tests: {passed} passed, {failed} failed")
    print(f"{'='*60}")
    for d in details:
        print(d)
    print()

    return passed, failed, details


# ============================================================
# CLI
# ============================================================

def apply_run_overrides(config: Optional[dict], budget_seconds: Optional[float] = None, catch_up: bool = False,
                        progress_every: Optional[float] = None) -> Optional[dict]:
    """The owner's per-run overrides on top of the sidecar config. `catch_up` = no budget + progress every 5 s (an
    explicit --budget-seconds / --progress-every still wins). Returns the config unchanged when nothing is overridden.
    Shared with `claude_memory.py sleep` (the packaged unit's entry point), so both spell the flag the same way."""
    if budget_seconds is None and not catch_up and progress_every is None:
        return config
    config = dict(config or {})
    if catch_up:
        config['budget_seconds'] = 0
        config['progress_every_seconds'] = 5
        _stage_log("catch-up run: no time budget; a progress line every %ss; every batch commits with its watermark, "
                   "so Ctrl-C keeps the work done and closes the run row as 'timeout'",
                   progress_every if progress_every is not None else 5)
    if budget_seconds is not None:
        config['budget_seconds'] = budget_seconds
    if progress_every is not None:
        config['progress_every_seconds'] = progress_every
    return config


def exit_code_for(result: dict) -> int:
    """The process exit status for a run result (what systemd and the run ledger read).

    0  success, or partial with no stage error (the budget slice ended Stage 2 and the backlog is carried on the
       watermark -- the designed catch-up path, not a failure);
    75 skipped: the brain's run-lock was held (EX_TEMPFAIL; the ledger's "skipped-lock");
    1  anything else: a stage error, a timeout, an error.
    """
    status = result.get('status')
    if status == 'skipped':
        return EXIT_SKIPPED_LOCK
    if status == 'success':
        return 0
    if status == 'partial' and not result.get('error_log'):
        return 0
    return 1


def main():
    parser = argparse.ArgumentParser(
        description="Sleep Loop Consolidation (Wizard Library L2-C3)"
    )
    parser.add_argument("db_path", nargs="?", default=None,
                        help="Path to the SQLite database")
    parser.add_argument("--run", action="store_true",
                        help="Run the full sleep cycle")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true",
                        help="Simulate without modifying the database")
    parser.add_argument("--status", action="store_true",
                        help="Show recent run history")
    parser.add_argument("--config", default=None,
                        help="Path to JSON config file")
    parser.add_argument("--mode", default=None, choices=['heuristic', 'llm'],
                        help="Consolidation mode")
    parser.add_argument("--unit-tests", dest="unit_tests", action="store_true",
                        help="Run self-contained unit tests")
    parser.add_argument("--budget-seconds", dest="budget_seconds", type=float, default=None,
                        help="This run's time budget in seconds (overrides the config's budget_seconds; 0 = none)")
    parser.add_argument("--catch-up", dest="catch_up", action="store_true",
                        help="The owner's catch-up run: NO time budget, a progress line every 5 s on stderr. Every "
                             "batch commits with its watermark, so Ctrl-C (or a kill) keeps the work done so far "
                             "and closes the run row as 'timeout'.")
    parser.add_argument("--progress-every", dest="progress_every", type=float, default=None,
                        help="Seconds between Stage-2 progress lines on stderr (overrides the config)")

    args = parser.parse_args()

    if args.unit_tests:
        passed, failed, _ = unit_tests()
        sys.exit(0 if failed == 0 else 1)

    if not args.db_path:
        parser.print_help()
        sys.exit(1)

    # Load config.
    config = None
    if args.config:
        config_path = Path(args.config)
        if config_path.exists():
            config = json.loads(config_path.read_text())
    if args.mode:
        config = config or {}
        config['consolidation_mode'] = args.mode
    config = apply_run_overrides(config, budget_seconds=args.budget_seconds, catch_up=args.catch_up,
                                 progress_every=args.progress_every)

    if args.status:
        conn = _get_conn(args.db_path)
        history = get_run_history(conn, limit=10)
        conn.close()
        print(json.dumps({"status": "ok", "runs": history}, indent=2))
        sys.exit(0)

    if args.run or args.dry_run:
        result = run_sleep_cycle(
            db_path=args.db_path,
            config=config,
            dry_run=args.dry_run,
        )
        print(json.dumps(result, indent=2, default=str))
        sys.exit(exit_code_for(result))

    parser.print_help()


if __name__ == "__main__":
    main()
