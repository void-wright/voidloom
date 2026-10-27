#!/usr/bin/env python3
"""Whisper Network — Wizard Library L3 Component 8.

A privileged self-observation channel where Claude can log quiet observations
about its own behavior during sessions. Observations accumulate over the week;
a Friday 8 AM local time cron synthesizes them into a single reflection memory
(kind='reflection', tagged 'whisper-digest') that surfaces to Drew.

Public API
----------
``capture_whisper(conn, observation, observation_type, session_context=None, intensity=0.5) -> int``
    Store a single quiet observation. Returns the observation ID.

``get_undigested(conn, since=None) -> List[dict]``
    Retrieve all undigested observations, optionally since a given datetime.

``generate_weekly_digest(db_path, mode='heuristic', dry_run=False) -> dict``
    Synthesize undigested observations into one reflection memory.
    Marks consumed observations as digested.

``get_digest_history(conn, limit=5) -> List[dict]``
    Query recent whisper digests with their metadata.

``is_whisper_migration_applied(conn) -> bool``
    Check whether migration_009 has already been applied.

CLI interface (via claude_memory.py)
------------------------------------
::

    claude_memory.py whisper --type uncertainty --text "Not sure about approach"
    claude_memory.py whisper --type pattern --text "Recurring theme observed"
    claude_memory.py whisper-digest              # show latest digest
    claude_memory.py whisper-digest --week 2     # show 2nd-most-recent

Stand-alone testing
-------------------
::

    python3 whisper_network.py --unit-tests
    python3 whisper_network.py --digest <db_path>            # manual trigger
    python3 whisper_network.py --digest <db_path> --dry-run  # simulate
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import struct
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
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# --- Voidloom #176 A3: whisper-digest low-trust floor ----------------------
# A weekly whisper digest is a self-generated reflection on the brain's own
# subjective experience — a synthesis, not an authored or external fact. The
# digest INSERT already carries a lowish confidence (0.8), but WITHOUT a
# derived_from sentinel trust_tier._authorship_floor still classified it as
# AUTHORED (floor 0.85). Stamping derived_from='whisper_synthesis' makes
# compute_trust treat it as DERIVED (authorship floored at the reflection
# kind-prior ~0.60), so the digest ranks BELOW an authored Drew memory (~0.92).
# Mirrors the dream floor (A3) and the spirit of warden.stamp_untrusted_source:
# low trust + visible provenance, never gating the write.
WHISPER_DERIVED_FROM = "whisper_synthesis"
# --- end Voidloom #176 A3 constant -----------------------------------------

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

# Observation type taxonomy.
VALID_OBSERVATION_TYPES = (
    'uncertainty',  # don't know — unsure about approach/decision
    'friction',     # feels hard — unexpected resistance or complexity
    'surprise',     # unexpected — something didn't match predictions
    'pattern',      # recurring — noticed this before, naming it now
    'assumption',   # unverified — acting on belief without confirmation
    'blocked',      # stuck — no clear path forward
    'insight',      # aha — sudden understanding or connection
    'meta',         # about the process — observation about observing
)

# Nomic Embed v1.5 dimension (needed for embedding the digest memory).
_NOMIC_DIM = 768

# Voidloom #176 (RCE/iter2): pin the embedder to a verified HuggingFace commit
# SHA. nomic-embed-text-v1.5 carries `custom_code` and loads via the remote-code
# path (trust_remote_code=True is required -- trust_remote_code=False makes
# sentence-transformers raise). Pinning `revision` to a fixed commit makes the
# remote code tree IMMUTABLE, so a moved/replaced `main` tag can no longer swap
# modeling_*.py for an attacker payload (the RCE Thessaly named). The residual
# (the pinned, auditable remote code still runs) is documented in the seal.
# SHA verified 2026-06-11 against HF /api/models/.../refs AND /commits/main.
_NOMIC_REVISION = "e9b6763023c676ca8431644204f50c2b100d9aab"


def _torch_device(torch_mod) -> str:
    """THE device for this organ (handoff round 1, lane L1): voidloom_device.torch_device() -- the backend's pin first,
    then the backend's own API (CUDA, ROCm, MPS, XPU); 'cpu' on any degraded, refused or unconfirmed basis. Replaces
    the per-organ `"cuda:0" if _cuda_usable(torch) else "cpu"`, which could only ever say CUDA or CPU."""
    try:
        from voidloom_device import torch_device as _td
    except ImportError:
        import sys as _s
        _s.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from voidloom_device import torch_device as _td
    return _td(torch_mod)


def is_whisper_migration_applied(conn: sqlite3.Connection) -> bool:
    """Check if migration_009 (whisper_network) has been applied."""
    try:
        conn.execute("SELECT 1 FROM whisper_observations LIMIT 0")
        conn.execute("SELECT 1 FROM whisper_digests LIMIT 0")
        return True
    except sqlite3.OperationalError:
        return False


def capture_whisper(
    conn: sqlite3.Connection,
    observation: str,
    observation_type: str,
    session_context: Optional[str] = None,
    intensity: float = 0.5,
) -> int:
    """Capture a single quiet observation. Fast, lightweight, no embedding.

    Args:
        conn: sqlite3 connection with foreign_keys ON.
        observation: the text of the observation.
        observation_type: one of VALID_OBSERVATION_TYPES.
        session_context: what was being worked on (optional).
        intensity: 0.0-1.0, how strongly felt (default 0.5).

    Returns:
        The new observation row ID.

    Raises:
        ValueError: if observation_type or intensity is invalid.
    """
    if observation_type not in VALID_OBSERVATION_TYPES:
        raise ValueError(
            f"Invalid observation_type '{observation_type}'. "
            f"Valid: {VALID_OBSERVATION_TYPES}"
        )
    if not (0.0 <= intensity <= 1.0):
        raise ValueError(f"Intensity must be 0.0-1.0, got {intensity}")
    if not observation or not observation.strip():
        raise ValueError("Observation text must not be empty")

    cursor = conn.execute(
        """INSERT INTO whisper_observations
           (observation, observation_type, session_context, intensity)
           VALUES (?, ?, ?, ?)""",
        (observation.strip(), observation_type, session_context, intensity),
    )
    conn.commit()
    return cursor.lastrowid


def get_undigested(
    conn: sqlite3.Connection,
    since: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Retrieve all undigested observations.

    Args:
        conn: sqlite3 connection.
        since: ISO datetime string; only return observations after this time.

    Returns:
        List of observation dicts with keys: id, observation, observation_type,
        session_context, intensity, created_at.
    """
    if since:
        rows = conn.execute(
            """SELECT id, observation, observation_type, session_context,
                      intensity, created_at
               FROM whisper_observations
               WHERE digested = 0 AND created_at > ?
               ORDER BY intensity DESC, created_at ASC""",
            (since,),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT id, observation, observation_type, session_context,
                      intensity, created_at
               FROM whisper_observations
               WHERE digested = 0
               ORDER BY intensity DESC, created_at ASC""",
        ).fetchall()

    return [
        {
            'id': r[0],
            'observation': r[1],
            'observation_type': r[2],
            'session_context': r[3],
            'intensity': r[4],
            'created_at': r[5],
        }
        for r in rows
    ]


def get_digest_history(
    conn: sqlite3.Connection,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """Query recent whisper digests.

    Args:
        conn: sqlite3 connection.
        limit: max digests to return.

    Returns:
        List of digest dicts with keys: id, digest_memory_id, observations_count,
        period_start, period_end, created_at, plus the digest memory content
        if the memory still exists.
    """
    rows = conn.execute(
        """SELECT d.id, d.digest_memory_id, d.observations_count,
                  d.period_start, d.period_end, d.created_at,
                  m.content, m.summary
           FROM whisper_digests d
           LEFT JOIN memories m ON d.digest_memory_id = m.id
           ORDER BY d.created_at DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()

    return [
        {
            'id': r[0],
            'digest_memory_id': r[1],
            'observations_count': r[2],
            'period_start': r[3],
            'period_end': r[4],
            'created_at': r[5],
            'content': r[6],
            'summary': r[7],
        }
        for r in rows
    ]


def _group_observations_by_type(
    observations: List[Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    """Group observations by type for digest synthesis."""
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for obs in observations:
        groups[obs['observation_type']].append(obs)
    return dict(groups)


def _compute_period_bounds(
    observations: List[Dict[str, Any]],
) -> Tuple[str, str]:
    """Compute the earliest and latest created_at from observations."""
    if not observations:
        now = datetime.now().isoformat()
        return now, now
    dates = [obs['created_at'] for obs in observations]
    return min(dates), max(dates)


def _synthesize_heuristic(
    observations: List[Dict[str, Any]],
    grouped: Dict[str, List[Dict[str, Any]]],
    period_start: str,
    period_end: str,
) -> Tuple[str, str]:
    """Heuristic digest synthesis — no LLM call.

    Returns (content, summary) tuple.
    """
    total = len(observations)
    type_counts = {t: len(obs_list) for t, obs_list in grouped.items()}
    type_summary = ", ".join(
        f"{t}({c})" for t, c in sorted(type_counts.items(), key=lambda x: -x[1])
    )

    # Find the highest-intensity observations (top 5).
    top_intensity = sorted(observations, key=lambda x: -x['intensity'])[:5]

    # Session contexts (deduplicated).
    contexts = list(dict.fromkeys(
        obs['session_context'] for obs in observations
        if obs['session_context']
    ))[:8]

    # Build content sections.
    sections = []
    sections.append(
        f"Whisper Network Weekly Digest ({period_start[:10]} to {period_end[:10]}): "
        f"{total} self-observations captured."
    )
    sections.append(f"Distribution: {type_summary}.")

    if top_intensity:
        sections.append("Highest-intensity observations:")
        for obs in top_intensity:
            sections.append(
                f"  [{obs['observation_type']}, {obs['intensity']:.1f}] "
                f"{obs['observation'][:120]}"
            )

    if contexts:
        sections.append(f"Session contexts: {'; '.join(contexts[:5])}.")

    # Per-type clusters with representative samples.
    for obs_type in ('uncertainty', 'friction', 'blocked', 'pattern', 'insight'):
        if obs_type in grouped and len(grouped[obs_type]) >= 2:
            type_obs = grouped[obs_type]
            sections.append(
                f"{obs_type.capitalize()} cluster ({len(type_obs)}): "
                f"{type_obs[0]['observation'][:80]}; "
                f"{type_obs[1]['observation'][:80]}"
                + (f"; +{len(type_obs) - 2} more" if len(type_obs) > 2 else "")
            )

    content = "\n".join(sections)
    summary = (
        f"Whisper digest: {total} observations ({type_summary}). "
        f"Period: {period_start[:10]} to {period_end[:10]}."
    )[:200]

    return content, summary


def _synthesize_llm(
    observations: List[Dict[str, Any]],
    grouped: Dict[str, List[Dict[str, Any]]],
    period_start: str,
    period_end: str,
) -> Optional[Tuple[str, str]]:
    """LLM digest synthesis through the thinker seam (text only, the haiku tier). Returns None on failure."""

    total = len(observations)
    type_counts = {t: len(obs_list) for t, obs_list in grouped.items()}

    # Build a concise representation for the LLM.
    obs_lines = []
    for obs in sorted(observations, key=lambda x: -x['intensity'])[:20]:
        obs_lines.append(
            f"[{obs['observation_type']}, intensity={obs['intensity']:.1f}] "
            f"{obs['observation'][:150]}"
        )

    prompt = (
        f"You are synthesizing a weekly whisper digest — Claude's self-observations "
        f"about its own behavior, captured over {period_start[:10]} to {period_end[:10]}.\n\n"
        f"Total observations: {total}. Distribution: "
        f"{', '.join(f'{t}={c}' for t, c in type_counts.items())}.\n\n"
        f"Top observations (by intensity):\n"
        + "\n".join(obs_lines) +
        "\n\nSynthesize these into a concise (3-5 paragraph) weekly digest that:\n"
        "1. Identifies the dominant theme(s) of the week\n"
        "2. Highlights concerning patterns (recurring uncertainty, growing friction)\n"
        "3. Notes any insights or breakthroughs\n"
        "4. Suggests one concrete thing to address next week\n\n"
        "Write in first person as Claude reflecting on its own behavior. "
        "Be honest and specific — this is for the owner to understand blind spots."
    )

    try:
        res = _thinker.think(prompt, purpose="whisper_network", tier="haiku", timeout_s=90)
    except _thinker.ThinkError as e:
        logger.warning("LLM digest synthesis: thinker %s (%s); falling back to heuristic", e.kind, e)
        return None
    content = res.text.strip()
    if content:
        summary = content.split('\n')[0][:200]
        return content, summary
    return None


def _serialize_f32(vec) -> bytes:
    """Serialize a float32 vector for sqlite-vec."""
    return struct.pack(f"{len(vec)}f", *vec)


def _encode_for_embedding(text: str, conn=None) -> Optional[bytes]:
    """Encode `text` for THE TIER THE CALLER'S DATABASE IS ON.

    ROUND GAMMA (A6). This function used to end in `vec[:_NOMIC_DIM]` -- a hard
    768 -- while the brain moved to jina/1024 on 2026-05-28. beta routed the
    INSERT to the resolved table and left this truncation in place, so sqlite-vec
    would raise "Expected 1024 dimensions but received 768" and the bare
    `except sqlite3.OperationalError: pass` below would eat it.

    CORRECTED 2026-09-06 (curation_BETA, F-C1). This docstring previously read
    "MEASURED: 0 of 15 whisper digests have an embedding in EITHER table. This
    organ has never written one." THAT IS FALSE. All 15 digests carry a
    3,072-byte (768-float) row in memory_embeddings, and one also has a
    4,096-byte row in v2. The measurement behind it filtered the vec0 SHADOW
    tables on `id`, which is NULL on every row in both tables -- vec0 keeps the
    INTEGER PRIMARY KEY in the shadow ROWID -- so that predicate returns 0 for
    every memory in the brain and can never return anything else.

    The correction inverts the harm model, and the inversion is the point: this
    organ was a WORKING 768-wide writer. beta's patch would have re-pointed it at
    a float[1024] table that rejects it, behind the swallow below. Holding
    whisper_network.py out of beta's landing prevented a LIVE REGRESSION rather
    than merely deferring an inert change.

    Truncating jina to 768 or padding nomic to 1024 both produce a vector that is
    not in the table's space, so the repair is not a width adjustment -- it is
    using the RIGHT ENCODER. memory_write.default_encoder resolves the tier from
    the caller's connection and routes to the matching model.

    `conn=None` keeps the old signature working for any caller that has no
    connection to offer; it then falls back to the local nomic path and the
    width assertion downstream refuses rather than the write failing silently.
    """
    if conn is not None:
        try:
            import sys as _s, os as _o
            _d = _o.path.dirname(_o.path.abspath(__file__))
            if _d not in _s.path:
                _s.path.insert(0, _d)
            import memory_write
            return memory_write.default_encoder(text, conn)
        except Exception as e:
            logger.error("whisper: tier-correct encode failed (%s: %s); "
                         "falling back to the local nomic path, which will be "
                         "REFUSED downstream if this brain is on jina/1024.",
                         type(e).__name__, e)
    try:
        from sentence_transformers import SentenceTransformer
        import torch
        import numpy as np

        # setdefault is FORBIDDEN (#15708): it yields to an existing OR EMPTY
        # value, so an exported-but-empty variable silently wins.  Resolve.
        from voidloom_device import apply_cuda_pin as _apply
        _apply()
        device = _torch_device(torch)
        model = SentenceTransformer(
            "nomic-ai/nomic-embed-text-v1.5",
            revision=_NOMIC_REVISION,  # Voidloom #176 RCE: pin code tree (moved tag can't swap it)
            trust_remote_code=True,
            device=device,
        )
        query = f"search_document: {text}"
        vec = model.encode(query, normalize_embeddings=True)
        return _serialize_f32(vec[:_NOMIC_DIM].astype(np.float32).tolist())
    except Exception as e:
        logger.warning("Embedding failed: %s", e)
        return None


def _thinker_week(db_path: str, live: bool) -> Dict[str, Any]:
    """M14 (model-choice unit): the weekly digest carries the overnight thinker's line, and -- on the brain whose
    THINKER_RECORD it is, and only when `live` -- runs the weekly credential check (one tool-free probe, logged;
    alerts on the FIRST auth failure and ~30 days before Claude's one-year token expires). A digest of a test or
    copied database never probes the owner's thinker."""
    try:
        own = os.path.dirname(os.path.abspath(db_path)) == str(_thinker.brain_dir())
        if live and own:
            r = _thinker.weekly_check()
            return {"line": r.get("line") or _thinker.health_line(), "alerts": r.get("alerts", []),
                    "checked": "skipped" not in r}
        return {"line": _thinker.health_line(), "alerts": [], "checked": False}
    except Exception as e:  # noqa: BLE001 - the digest never fails on the thinker's account
        return {"line": "thinker: the weekly check could not run (%s: %s)" % (type(e).__name__, e),
                "alerts": [], "checked": False}


def generate_weekly_digest(
    db_path: str,
    mode: str = 'heuristic',
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Synthesize undigested observations into one reflection memory.

    Args:
        db_path: path to the sqlite database.
        mode: 'heuristic' (fast, no LLM) or 'llm' (the recorded overnight thinker, through organs/thinker.py).
        dry_run: if True, compute the digest but don't persist.

    Returns:
        Dict with status, observations_count, digest details.
    """
    t0 = time.time()

    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.execute("PRAGMA foreign_keys=ON")

    # Load sqlite-vec for embedding.
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

    if not is_whisper_migration_applied(conn):
        conn.close()
        return {
            'status': 'error',
            'message': 'whisper_network migration not applied. Run init first.',
        }

    observations = get_undigested(conn)

    if not observations:
        conn.close()
        return {
            'status': 'ok',
            'action': 'whisper-digest',
            'message': 'No undigested observations found.',
            'observations_count': 0,
            'thinker': _thinker_week(db_path, live=not dry_run),
            'time_ms': int((time.time() - t0) * 1000),
        }

    grouped = _group_observations_by_type(observations)
    period_start, period_end = _compute_period_bounds(observations)

    # Synthesize the digest.
    content: Optional[str] = None
    summary: Optional[str] = None

    if mode == 'llm':
        result = _synthesize_llm(observations, grouped, period_start, period_end)
        if result:
            content, summary = result

    if content is None:
        content, summary = _synthesize_heuristic(
            observations, grouped, period_start, period_end
        )
    thinker_week = _thinker_week(db_path, live=not dry_run)
    content = content + "\n\n---\nOvernight thinker: " + thinker_week["line"] + "".join(
        "\n" + a for a in thinker_week["alerts"])

    if dry_run:
        conn.close()
        return {
            'status': 'ok',
            'action': 'whisper-digest',
            'dry_run': True,
            'observations_count': len(observations),
            'period_start': period_start,
            'period_end': period_end,
            'type_distribution': {t: len(g) for t, g in grouped.items()},
            'digest_preview': content[:500],
            'thinker': thinker_week,
            'time_ms': int((time.time() - t0) * 1000),
        }

    # Store the digest as a proper reflection memory.
    embedding = _encode_for_embedding(content, conn)

    cursor = conn.execute(
        """INSERT INTO memories
           (content, summary, category, importance, source_project, source_context,
            kind, confidence, derived_from)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            content,
            summary,
            'insight',
            0.7,
            'whisper-network-digest',
            f'observations_count={len(observations)}',
            'reflection',
            0.8,
            # Voidloom #176 A3: non-authored sentinel -> trust floors below Drew.
            WHISPER_DERIVED_FROM,
        ),
    )
    digest_memory_id = cursor.lastrowid

    # ROUND GAMMA (A6). What was here:
    #
    #     if embedding:
    #         try:
    #             conn.execute("INSERT INTO memory_embeddings (...)", ...)
    #         except sqlite3.OperationalError:
    #             pass   # vec0 table not present (e.g. test DB without sqlite_vec)
    #
    # Three defects in six lines: a HARDCODED table retired 2026-05-28; a
    # comment claiming the handler catches an absent test table when it also
    # catches a real dimension mismatch on the real brain; and `pass`, which is
    # why 15 digests in a row produced nothing and nobody knew.
    #
    # attach_embedding resolves the table from THIS connection, asserts the
    # vector's width against the resolved dim, and on failure logs at ERROR and
    # tags the row 'embedding-missing' so the gap is findable by SQL. A test DB
    # without sqlite_vec still does not crash the digest -- it is reported.
    import sys as _s, os as _o
    _d = _o.path.dirname(_o.path.abspath(__file__))
    if _d not in _s.path:
        _s.path.insert(0, _d)
    try:
        import memory_write as _mw
        _emb_res = _mw.on_memory_written(
            conn, digest_memory_id, content,
            organ="whisper_network.generate_weekly_digest",
            embedding=embedding if embedding else None,
        )
        if not _emb_res.get("embedded"):
            logger.error("whisper digest %s stored WITHOUT an embedding: %s",
                         digest_memory_id, _emb_res.get("error"))
    except Exception as _e:
        logger.error("whisper: memory_write unavailable (%s: %s); digest %s has "
                     "no embedding and could not be marked.",
                     type(_e).__name__, _e, digest_memory_id)

    # Tag the digest memory.
    for tag in ('whisper-digest', 'self-observation', 'weekly-reflection'):
        conn.execute(
            "INSERT OR IGNORE INTO memory_tags (memory_id, tag) VALUES (?, ?)",
            (digest_memory_id, tag),
        )

    # Record the digest in whisper_digests.
    cursor2 = conn.execute(
        """INSERT INTO whisper_digests
           (digest_memory_id, observations_count, period_start, period_end)
           VALUES (?, ?, ?, ?)""",
        (digest_memory_id, len(observations), period_start, period_end),
    )
    digest_id = cursor2.lastrowid

    # Mark all consumed observations as digested.
    obs_ids = [obs['id'] for obs in observations]
    placeholders = ','.join('?' * len(obs_ids))
    conn.execute(
        f"""UPDATE whisper_observations
            SET digested = 1, digest_id = ?
            WHERE id IN ({placeholders})""",
        [digest_id] + obs_ids,
    )

    conn.commit()
    conn.close()

    return {
        'status': 'ok',
        'action': 'whisper-digest',
        'digest_memory_id': digest_memory_id,
        'digest_id': digest_id,
        'observations_count': len(observations),
        'period_start': period_start,
        'period_end': period_end,
        'type_distribution': {t: len(g) for t, g in grouped.items()},
        'mode': mode,
        'thinker': thinker_week,
        'has_embedding': embedding is not None,
        'time_ms': int((time.time() - t0) * 1000),
    }


# ============================================================
# Unit Tests
# ============================================================

def _run_unit_tests():
    """Self-contained unit tests. Run with --unit-tests flag."""
    import tempfile

    print("=" * 60)
    print("Whisper Network — Unit Tests")
    print("=" * 60)
    passed = 0
    failed = 0
    test_db = None

    try:
        # Create test database.
        test_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        test_db_path = test_db.name
        test_db.close()

        conn = sqlite3.connect(test_db_path)
        conn.execute("PRAGMA foreign_keys=ON")

        # Apply base schema.
        base_schema = Path(__file__).parent.parent / "migrations" / "schema.sql"
        if base_schema.exists():
            conn.executescript(base_schema.read_text())
        else:
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
                    confidence REAL DEFAULT 1.0
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
            """)

        # Apply whisper migration.
        migration_path = Path(__file__).parent.parent / "migrations" / "migration_009_whisper_network.sql"
        if migration_path.exists():
            conn.executescript(migration_path.read_text())
        else:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS whisper_observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    observation TEXT NOT NULL,
                    observation_type TEXT NOT NULL CHECK(observation_type IN (
                        'uncertainty', 'friction', 'surprise', 'pattern', 'assumption',
                        'blocked', 'insight', 'meta'
                    )),
                    session_context TEXT DEFAULT NULL,
                    intensity REAL DEFAULT 0.5 CHECK(intensity >= 0.0 AND intensity <= 1.0),
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    digested INTEGER DEFAULT 0,
                    digest_id INTEGER DEFAULT NULL
                );
                CREATE TABLE IF NOT EXISTS whisper_digests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    digest_memory_id INTEGER REFERENCES memories(id),
                    observations_count INTEGER NOT NULL,
                    period_start TEXT NOT NULL,
                    period_end TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                );
            """)
        conn.commit()

        # --- Test 1: is_whisper_migration_applied ---
        print("\n[T01] is_whisper_migration_applied on fresh DB...")
        assert is_whisper_migration_applied(conn), "Migration should be detected"
        passed += 1
        print("  PASS")

        # --- Test 2: capture basic observation ---
        print("[T02] capture_whisper basic...")
        obs_id = capture_whisper(
            conn, "Not sure if RRF k=15 is optimal", "uncertainty",
            session_context="hybrid-retrieval tuning", intensity=0.7,
        )
        assert obs_id >= 1, f"Expected positive ID, got {obs_id}"
        row = conn.execute(
            "SELECT observation, observation_type, intensity, digested FROM whisper_observations WHERE id=?",
            (obs_id,),
        ).fetchone()
        assert row[0] == "Not sure if RRF k=15 is optimal"
        assert row[1] == "uncertainty"
        assert abs(row[2] - 0.7) < 0.001
        assert row[3] == 0
        passed += 1
        print("  PASS")

        # --- Test 3: invalid observation_type rejected ---
        print("[T03] capture_whisper rejects invalid type...")
        try:
            capture_whisper(conn, "test", "invalid_type")
            failed += 1
            print("  FAIL — should have raised ValueError")
        except ValueError as e:
            assert "invalid_type" in str(e).lower() or "Invalid" in str(e)
            passed += 1
            print("  PASS")

        # --- Test 4: invalid intensity rejected ---
        print("[T04] capture_whisper rejects intensity > 1.0...")
        try:
            capture_whisper(conn, "test", "friction", intensity=1.5)
            failed += 1
            print("  FAIL — should have raised ValueError")
        except ValueError:
            passed += 1
            print("  PASS")

        # --- Test 5: empty observation rejected ---
        print("[T05] capture_whisper rejects empty text...")
        try:
            capture_whisper(conn, "   ", "meta")
            failed += 1
            print("  FAIL — should have raised ValueError")
        except ValueError:
            passed += 1
            print("  PASS")

        # --- Test 6: get_undigested returns only undigested ---
        print("[T06] get_undigested filters correctly...")
        capture_whisper(conn, "Pattern noticed", "pattern", intensity=0.9)
        capture_whisper(conn, "Feeling blocked", "blocked", intensity=0.3)
        # Mark one as digested.
        conn.execute("UPDATE whisper_observations SET digested=1 WHERE id=?", (obs_id,))
        conn.commit()
        undigested = get_undigested(conn)
        undigested_ids = [u['id'] for u in undigested]
        assert obs_id not in undigested_ids, "Digested observation should not appear"
        assert len(undigested) == 2, f"Expected 2 undigested, got {len(undigested)}"
        passed += 1
        print("  PASS")

        # --- Test 7: get_undigested ordered by intensity DESC ---
        print("[T07] get_undigested ordered by intensity...")
        assert undigested[0]['intensity'] >= undigested[1]['intensity']
        passed += 1
        print("  PASS")

        # --- Test 8: generate_weekly_digest dry_run ---
        print("[T08] generate_weekly_digest dry_run...")
        # Reset all to undigested for clean test.
        conn.execute("UPDATE whisper_observations SET digested=0, digest_id=NULL")
        conn.commit()
        result = generate_weekly_digest(test_db_path, mode='heuristic', dry_run=True)
        assert result['status'] == 'ok'
        assert result['dry_run'] is True
        assert result['observations_count'] == 3
        assert 'digest_preview' in result
        passed += 1
        print("  PASS")

        # --- Test 9: generate_weekly_digest actual run ---
        print("[T09] generate_weekly_digest actual (heuristic)...")
        result = generate_weekly_digest(test_db_path, mode='heuristic', dry_run=False)
        assert result['status'] == 'ok'
        assert result['observations_count'] == 3
        assert result['digest_memory_id'] is not None
        assert result['digest_id'] is not None
        # Verify the digest memory was stored.
        mem_row = conn.execute(
            "SELECT kind, source_project FROM memories WHERE id=?",
            (result['digest_memory_id'],),
        ).fetchone()
        assert mem_row[0] == 'reflection'
        assert mem_row[1] == 'whisper-network-digest'
        # Verify tags.
        tags = [r[0] for r in conn.execute(
            "SELECT tag FROM memory_tags WHERE memory_id=?",
            (result['digest_memory_id'],),
        ).fetchall()]
        assert 'whisper-digest' in tags
        assert 'self-observation' in tags
        passed += 1
        print("  PASS")

        # --- Test 10: observations marked as digested ---
        print("[T10] observations marked digested after digest...")
        remaining = get_undigested(conn)
        assert len(remaining) == 0, f"Expected 0 undigested, got {len(remaining)}"
        passed += 1
        print("  PASS")

        # --- Test 11: second digest with no observations ---
        print("[T11] second digest with no new observations...")
        result2 = generate_weekly_digest(test_db_path, mode='heuristic', dry_run=False)
        assert result2['observations_count'] == 0
        assert 'No undigested' in result2.get('message', '')
        passed += 1
        print("  PASS")

        # --- Test 12: get_digest_history ---
        print("[T12] get_digest_history returns stored digest...")
        history = get_digest_history(conn, limit=5)
        assert len(history) >= 1
        assert history[0]['observations_count'] == 3
        assert history[0]['digest_memory_id'] == result['digest_memory_id']
        passed += 1
        print("  PASS")

        # --- Test 13: capture_whisper with no session_context ---
        print("[T13] capture_whisper with no context...")
        obs_id2 = capture_whisper(conn, "Just an insight", "insight")
        row2 = conn.execute(
            "SELECT session_context FROM whisper_observations WHERE id=?",
            (obs_id2,),
        ).fetchone()
        assert row2[0] is None
        passed += 1
        print("  PASS")

        # --- Test 14: all observation types accepted ---
        print("[T14] all 8 observation types valid...")
        for otype in VALID_OBSERVATION_TYPES:
            oid = capture_whisper(conn, f"Test {otype}", otype)
            assert oid > 0
        passed += 1
        print("  PASS")

        # --- Test 15: intensity edge cases (0.0 and 1.0) ---
        print("[T15] intensity boundary values 0.0 and 1.0...")
        id_low = capture_whisper(conn, "Low intensity", "meta", intensity=0.0)
        id_high = capture_whisper(conn, "High intensity", "meta", intensity=1.0)
        assert id_low > 0 and id_high > 0
        passed += 1
        print("  PASS")

        # --- Test 16: _group_observations_by_type ---
        print("[T16] _group_observations_by_type groups correctly...")
        test_obs = [
            {'observation_type': 'uncertainty', 'observation': 'a', 'intensity': 0.5},
            {'observation_type': 'uncertainty', 'observation': 'b', 'intensity': 0.6},
            {'observation_type': 'friction', 'observation': 'c', 'intensity': 0.4},
        ]
        groups = _group_observations_by_type(test_obs)
        assert len(groups['uncertainty']) == 2
        assert len(groups['friction']) == 1
        passed += 1
        print("  PASS")

        # --- Test 17: _compute_period_bounds ---
        print("[T17] _compute_period_bounds correct...")
        test_obs2 = [
            {'created_at': '2026-05-20T10:00:00'},
            {'created_at': '2026-05-26T15:30:00'},
            {'created_at': '2026-05-23T08:00:00'},
        ]
        start, end = _compute_period_bounds(test_obs2)
        assert start == '2026-05-20T10:00:00'
        assert end == '2026-05-26T15:30:00'
        passed += 1
        print("  PASS")

        # --- Test 18: _compute_period_bounds empty list ---
        print("[T18] _compute_period_bounds empty list...")
        start_e, end_e = _compute_period_bounds([])
        assert start_e == end_e  # both should be 'now'
        passed += 1
        print("  PASS")

        # --- Test 19: _synthesize_heuristic produces content ---
        print("[T19] _synthesize_heuristic produces non-empty content...")
        all_obs = get_undigested(conn)
        if all_obs:
            grouped2 = _group_observations_by_type(all_obs)
            s, e = _compute_period_bounds(all_obs)
            content, summary = _synthesize_heuristic(all_obs, grouped2, s, e)
            assert len(content) > 50
            assert len(summary) > 10
            assert 'Whisper' in content
            passed += 1
            print("  PASS")
        else:
            passed += 1
            print("  PASS (skipped — no undigested)")

        # --- Test 20: digest_id foreign key set on observations ---
        print("[T20] digest_id set on consumed observations...")
        consumed = conn.execute(
            "SELECT digest_id FROM whisper_observations WHERE digested=1 AND digest_id IS NOT NULL"
        ).fetchall()
        assert len(consumed) >= 1, "At least one should have digest_id set"
        passed += 1
        print("  PASS")

        # --- Test 21: generate_weekly_digest with since filter ---
        print("[T21] get_undigested with since filter...")
        future_since = '2099-01-01T00:00:00'
        filtered = get_undigested(conn, since=future_since)
        assert len(filtered) == 0
        passed += 1
        print("  PASS")

        # --- Test 22: multiple digests produce unique IDs ---
        print("[T22] multiple digests produce unique entries...")
        capture_whisper(conn, "New week observation A", "surprise", intensity=0.8)
        capture_whisper(conn, "New week observation B", "insight", intensity=0.6)
        result3 = generate_weekly_digest(test_db_path, mode='heuristic', dry_run=False)
        assert result3['status'] == 'ok'
        assert result3['digest_id'] != result['digest_id']
        history2 = get_digest_history(conn, limit=10)
        assert len(history2) >= 2
        passed += 1
        print("  PASS")

        # --- Test 23: observation text whitespace stripped ---
        print("[T23] observation text stripped...")
        obs_ws = capture_whisper(conn, "  padded text  ", "meta")
        row_ws = conn.execute(
            "SELECT observation FROM whisper_observations WHERE id=?", (obs_ws,)
        ).fetchone()
        assert row_ws[0] == "padded text"
        passed += 1
        print("  PASS")

        # --- Test 24: default intensity is 0.5 ---
        print("[T24] default intensity 0.5...")
        obs_def = capture_whisper(conn, "Default intensity", "pattern")
        row_def = conn.execute(
            "SELECT intensity FROM whisper_observations WHERE id=?", (obs_def,)
        ).fetchone()
        assert abs(row_def[0] - 0.5) < 0.001
        passed += 1
        print("  PASS")

        # --- Test 25: migration idempotent (re-apply) ---
        print("[T25] migration idempotent on re-apply...")
        migration_sql = """
            CREATE TABLE IF NOT EXISTS whisper_observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                observation TEXT NOT NULL,
                observation_type TEXT NOT NULL,
                session_context TEXT DEFAULT NULL,
                intensity REAL DEFAULT 0.5,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                digested INTEGER DEFAULT 0,
                digest_id INTEGER DEFAULT NULL
            );
            CREATE TABLE IF NOT EXISTS whisper_digests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                digest_memory_id INTEGER REFERENCES memories(id),
                observations_count INTEGER NOT NULL,
                period_start TEXT NOT NULL,
                period_end TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
        """
        conn.executescript(migration_sql)
        # Verify data still exists.
        count = conn.execute("SELECT COUNT(*) FROM whisper_observations").fetchone()[0]
        assert count > 0, "Data should survive re-apply"
        passed += 1
        print("  PASS")

    finally:
        if test_db:
            os.unlink(test_db.name)

    print(f"\n{'=' * 60}")
    print(f"Results: {passed} passed, {failed} failed out of {passed + failed}")
    print(f"{'=' * 60}")
    return passed, failed


# ============================================================
# CLI entry point (stand-alone mode)
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Whisper Network — L3 Component 8")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--unit-tests", action="store_true", help="Run unit tests")
    group.add_argument("--digest", metavar="DB_PATH", help="Generate weekly digest")
    group.add_argument("--capture", metavar="DB_PATH",
                       help="Capture a single whisper observation (requires --type + --body)")
    group.add_argument("--list", metavar="DB_PATH",
                       help="List recent whisper observations (default last 20)")

    parser.add_argument("--dry-run", action="store_true", help="Simulate without persisting")
    parser.add_argument("--mode", default="heuristic", choices=["heuristic", "llm"],
                        help="Synthesis mode (for --digest)")
    parser.add_argument("--type", choices=sorted(VALID_OBSERVATION_TYPES),
                        help="Whisper type (for --capture)")
    parser.add_argument("--body", help="Whisper observation text (for --capture)")
    parser.add_argument("--context", default=None,
                        help="Optional session context (for --capture)")
    parser.add_argument("--intensity", type=float, default=0.5,
                        help="Whisper intensity 0.0-1.0 (for --capture, default 0.5)")
    parser.add_argument("--limit", type=int, default=20,
                        help="Max rows for --list (default 20)")

    args = parser.parse_args()

    if args.unit_tests:
        passed, failed = _run_unit_tests()
        sys.exit(0 if failed == 0 else 1)
    elif args.digest:
        result = generate_weekly_digest(
            db_path=args.digest,
            mode=args.mode,
            dry_run=args.dry_run,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        sys.exit(0 if result.get('status') == 'ok' else 1)
    elif args.capture:
        if not args.type or not args.body:
            parser.error("--capture requires both --type and --body")
        conn = sqlite3.connect(args.capture, timeout=60.0)
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            obs_id = capture_whisper(
                conn,
                observation=args.body,
                observation_type=args.type,
                session_context=args.context,
                intensity=args.intensity,
            )
            print(json.dumps({
                "status": "ok",
                "action": "whisper-capture",
                "id": obs_id,
                "type": args.type,
                "body_preview": args.body[:120],
            }, indent=2, ensure_ascii=False))
            sys.exit(0)
        except ValueError as e:
            print(json.dumps({"status": "error", "message": str(e)}))
            sys.exit(1)
        finally:
            conn.close()
    elif args.list:
        conn = sqlite3.connect(args.list, timeout=60.0)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT id, observation_type, intensity, session_context, "
                "substr(observation, 1, 200) AS preview, captured_at "
                "FROM whisper_observations ORDER BY id DESC LIMIT ?",
                (args.limit,),
            ).fetchall()
            out = [dict(r) for r in rows]
            print(json.dumps({"status": "ok", "count": len(out), "whispers": out},
                             indent=2, ensure_ascii=False))
            sys.exit(0)
        finally:
            conn.close()


if __name__ == "__main__":
    main()
